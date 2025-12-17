from __future__ import annotations

import threading
from typing import Callable, List, Optional
from kivy.clock import Clock
from kivy.utils import platform

# Placeholder for listener references to prevent garbage collection
_listeners = []

class BillingManager:
    """
    Manages Google Play Billing via pyjnius for Kivy/Android.
    Handles connection, purchasing, and acknowledgement.
    """
    
    def __init__(self, update_callback: Callable[[str, str, str], None]):
        """
        :param update_callback: Function called on purchase success. 
                                Signature: (sku, purchase_token, order_id)
        """
        self.update_callback = update_callback
        self.connected = False
        self.billing_client = None
        self.sku_details_map = {}
        
        if platform == "android":
            self._init_android()
        else:
            print("BillingManager: Not on Android, billing disabled.")

    def _init_android(self):
        try:
            from jnius import autoclass, cast, PythonJavaClass, java_method
            
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            self.activity = PythonActivity.mActivity
            self.context = self.activity.getApplicationContext()
            
            BillingClient = autoclass('com.android.billingclient.api.BillingClient')
            PurchasesUpdatedListener = autoclass('com.android.billingclient.api.PurchasesUpdatedListener')
            
            # Inner class for callbacks
            class MyPurchasesUpdatedListener(PythonJavaClass):
                __javainterfaces__ = ['com.android.billingclient.api.PurchasesUpdatedListener']
                __javacontext__ = 'app'

                def __init__(self, manager):
                    self.manager = manager

                @java_method('(Lcom/android/billingclient/api/BillingResult;Ljava/util/List;)V')
                def onPurchasesUpdated(self, billingResult, purchases):
                    self.manager._on_purchases_updated(billingResult, purchases)

            self.listener = MyPurchasesUpdatedListener(self)
            _listeners.append(self.listener) # Keep reference
            
            builder = BillingClient.newBuilder(self.context)
            builder.setListener(self.listener)
            builder.enablePendingPurchases()
            self.billing_client = builder.build()
            
            self.start_connection()
            
        except Exception as e:
            print(f"BillingManager Error: {e}")

    def start_connection(self):
        if not self.billing_client:
            return

        from jnius import autoclass, PythonJavaClass, java_method
        BillingClientStateListener = autoclass('com.android.billingclient.api.BillingClientStateListener')
        BillingClient = autoclass('com.android.billingclient.api.BillingClient')

        class MyStateListener(PythonJavaClass):
            __javainterfaces__ = ['com.android.billingclient.api.BillingClientStateListener']
            __javacontext__ = 'app'

            def __init__(self, manager):
                self.manager = manager

            @java_method('(Lcom/android/billingclient/api/BillingResult;)V')
            def onBillingSetupFinished(self, billingResult):
                if billingResult.getResponseCode() == BillingClient.BillingResponseCode.OK:
                    print("BillingManager: Setup finished successfully.")
                    self.manager.connected = True
                else:
                    print(f"BillingManager: Setup failed: {billingResult.getDebugMessage()}")

            @java_method('()V')
            def onBillingServiceDisconnected(self):
                print("BillingManager: Service disconnected.")
                self.manager.connected = False
                # Retry logic could go here

        self.state_listener = MyStateListener(self)
        _listeners.append(self.state_listener)
        self.billing_client.startConnection(self.state_listener)

    def query_sku_details(self, sku_list: List[str]):
        if not self.connected or not self.billing_client:
            print("BillingManager: Cannot query, not connected.")
            return

        from jnius import autoclass, cast, java_method, PythonJavaClass
        
        SkuDetailsParams = autoclass('com.android.billingclient.api.SkuDetailsParams')
        BillingClient = autoclass('com.android.billingclient.api.BillingClient')
        ArrayList = autoclass('java.util.ArrayList')
        SkuDetailsResponseListener = autoclass('com.android.billingclient.api.SkuDetailsResponseListener')

        sku_list_java = ArrayList()
        for sku in sku_list:
            sku_list_java.add(sku)

        params = SkuDetailsParams.newBuilder()
        params.setSkusList(sku_list_java)
        params.setType(BillingClient.SkuType.SUBS) # Assuming subscriptions
        
        class MySkuDetailsResponseListener(PythonJavaClass):
            __javainterfaces__ = ['com.android.billingclient.api.SkuDetailsResponseListener']
            __javacontext__ = 'app'

            def __init__(self, manager):
                self.manager = manager

            @java_method('(Lcom/android/billingclient/api/BillingResult;Ljava/util/List;)V')
            def onSkuDetailsResponse(self, billingResult, skuDetailsList):
                if billingResult.getResponseCode() == BillingClient.BillingResponseCode.OK and skuDetailsList:
                    for skuDetails in skuDetailsList.toArray():
                        self.manager.sku_details_map[skuDetails.getSku()] = skuDetails
                    print(f"BillingManager: Loaded {len(skuDetailsList.toArray())} SKUs.")
                else:
                    print("BillingManager: Failed to load SKUs.")

        self.sku_listener = MySkuDetailsResponseListener(self)
        _listeners.append(self.sku_listener)
        self.billing_client.querySkuDetailsAsync(params.build(), self.sku_listener)

    def purchase(self, sku: str):
        if not self.connected or not self.billing_client:
            print("BillingManager: Not connected.")
            return

        details = self.sku_details_map.get(sku)
        if not details:
            print(f"BillingManager: SKU {sku} details not found. Call query_sku_details first.")
            # Attempt to query and then fail for now, or just fail
            return

        from jnius import autoclass
        BillingFlowParams = autoclass('com.android.billingclient.api.BillingFlowParams')
        BillingClient = autoclass('com.android.billingclient.api.BillingClient')
        
        flowParams = BillingFlowParams.newBuilder().setSkuDetails(details).build()
        responseCode = self.billing_client.launchBillingFlow(self.activity, flowParams).getResponseCode()
        
        if responseCode != BillingClient.BillingResponseCode.OK:
            print(f"BillingManager: Launch failed with code {responseCode}")

    def _on_purchases_updated(self, billingResult, purchases):
        from jnius import autoclass
        BillingClient = autoclass('com.android.billingclient.api.BillingClient')
        
        if billingResult.getResponseCode() == BillingClient.BillingResponseCode.OK and purchases:
            for purchase in purchases.toArray():
                self._handle_purchase(purchase)
        elif billingResult.getResponseCode() == BillingClient.BillingResponseCode.USER_CANCELED:
            print("BillingManager: User canceled.")
        else:
            print(f"BillingManager: Purchase failed: {billingResult.getDebugMessage()}")

    def _handle_purchase(self, purchase):
        from jnius import autoclass
        Purchase = autoclass('com.android.billingclient.api.Purchase')
        
        if purchase.getPurchaseState() == Purchase.PurchaseState.PURCHASED:
            # Acknowledge purchase if it's a subscription and hasn't been acknowledged
            if not purchase.isAcknowledged():
                self._acknowledge_purchase(purchase)
            else:
                # Already acknowledged, just notify
                self._notify_success(purchase)

    def _acknowledge_purchase(self, purchase):
        from jnius import autoclass, PythonJavaClass, java_method
        AcknowledgePurchaseParams = autoclass('com.android.billingclient.api.AcknowledgePurchaseParams')
        AcknowledgePurchaseResponseListener = autoclass('com.android.billingclient.api.AcknowledgePurchaseResponseListener')
        
        params = AcknowledgePurchaseParams.newBuilder().setPurchaseToken(purchase.getPurchaseToken()).build()
        
        class MyAckListener(PythonJavaClass):
            __javainterfaces__ = ['com.android.billingclient.api.AcknowledgePurchaseResponseListener']
            __javacontext__ = 'app'

            def __init__(self, manager, purchase):
                self.manager = manager
                self.purchase = purchase

            @java_method('(Lcom/android/billingclient/api/BillingResult;)V')
            def onAcknowledgePurchaseResponse(self, billingResult):
                from jnius import autoclass
                BillingClient = autoclass('com.android.billingclient.api.BillingClient')
                if billingResult.getResponseCode() == BillingClient.BillingResponseCode.OK:
                    print("BillingManager: Purchase acknowledged.")
                    self.manager._notify_success(self.purchase)
                else:
                    print("BillingManager: Acknowledge failed.")

        self.ack_listener = MyAckListener(self, purchase)
        _listeners.append(self.ack_listener)
        self.billing_client.acknowledgePurchase(params, self.ack_listener)

    def _notify_success(self, purchase):
        # Notify callback on main thread
        def callback_main(*_):
            sku = purchase.getSkus().get(0) # In newer billing client, might be getSkus() returning list
            # Note: In BillingClient 5+, getSkus() returns ArrayList. 
            # If using old wrapper, might be different. assuming standard list behavior.
            token = purchase.getPurchaseToken()
            order_id = purchase.getOrderId()
            if self.update_callback:
                self.update_callback(sku, token, order_id)
        
        Clock.schedule_once(callback_main, 0)
