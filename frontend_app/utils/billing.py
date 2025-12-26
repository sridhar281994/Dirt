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
        # True only if the BillingClient classes are present in the APK.
        self.available = False
        self.init_error: str = ""
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
            
            # If the billing library isn't packaged, autoclass will throw.
            # Treat that as "billing unavailable" rather than a hard error spam.
            BillingClient = autoclass('com.android.billingclient.api.BillingClient')
            PurchasesUpdatedListener = autoclass('com.android.billingclient.api.PurchasesUpdatedListener')
            self.available = True
            
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
            # Make this non-fatal; many builds won't include Google Billing.
            self.available = False
            self.connected = False
            self.billing_client = None
            self.init_error = str(e)
            print(f"BillingManager: Billing unavailable ({self.init_error})")

    def start_connection(self):
        if not self.available or not self.billing_client:
            return

        try:
            from jnius import autoclass, PythonJavaClass, java_method
            BillingClientStateListener = autoclass('com.android.billingclient.api.BillingClientStateListener')
            BillingClient = autoclass('com.android.billingclient.api.BillingClient')
        except Exception as exc:
            self.available = False
            self.connected = False
            self.init_error = str(exc)
            return

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
        try:
            self.billing_client.startConnection(self.state_listener)
        except Exception as exc:
            self.connected = False
            self.init_error = str(exc)

    def query_sku_details(self, product_ids: List[str]):
        """
        Query product details using BillingClient 5+ API (QueryProductDetailsParams)
        """
        if not self.available:
            return
        if not self.connected or not self.billing_client:
            print("BillingManager: Cannot query, not connected.")
            return

        from jnius import autoclass, cast, java_method, PythonJavaClass
        
        QueryProductDetailsParams = autoclass('com.android.billingclient.api.QueryProductDetailsParams')
        Product = autoclass('com.android.billingclient.api.QueryProductDetailsParams$Product')
        BillingClient = autoclass('com.android.billingclient.api.BillingClient')
        ArrayList = autoclass('java.util.ArrayList')
        
        product_list_java = ArrayList()
        for pid in product_ids:
            # Create Product object for each ID, assuming SUBS type
            product_builder = Product.newBuilder()
            product_builder.setProductId(pid)
            product_builder.setProductType(BillingClient.ProductType.SUBS)
            product_list_java.add(product_builder.build())

        params_builder = QueryProductDetailsParams.newBuilder()
        params_builder.setProductList(product_list_java)
        
        # ProductDetailsResponseListener
        class MyProductDetailsResponseListener(PythonJavaClass):
            __javainterfaces__ = ['com.android.billingclient.api.ProductDetailsResponseListener']
            __javacontext__ = 'app'

            def __init__(self, manager):
                self.manager = manager

            @java_method('(Lcom/android/billingclient/api/BillingResult;Ljava/util/List;)V')
            def onProductDetailsResponse(self, billingResult, productDetailsList):
                if billingResult.getResponseCode() == BillingClient.BillingResponseCode.OK and productDetailsList:
                    count = 0
                    for productDetails in productDetailsList.toArray():
                        self.manager.sku_details_map[productDetails.getProductId()] = productDetails
                        count += 1
                    print(f"BillingManager: Loaded {count} Products.")
                else:
                    print(f"BillingManager: Failed to load products. Code: {billingResult.getResponseCode()}")

        self.product_listener = MyProductDetailsResponseListener(self)
        _listeners.append(self.product_listener)
        self.billing_client.queryProductDetailsAsync(params_builder.build(), self.product_listener)

    def purchase(self, product_id: str):
        if not self.available:
            print("BillingManager: Billing unavailable.")
            return
        if not self.connected or not self.billing_client:
            print("BillingManager: Not connected.")
            return

        details = self.sku_details_map.get(product_id)
        if not details:
            print(f"BillingManager: Product {product_id} details not found. Call query_sku_details first.")
            return

        from jnius import autoclass
        BillingFlowParams = autoclass('com.android.billingclient.api.BillingFlowParams')
        ProductDetailsParams = autoclass('com.android.billingclient.api.BillingFlowParams$ProductDetailsParams')
        BillingClient = autoclass('com.android.billingclient.api.BillingClient')
        ArrayList = autoclass('java.util.ArrayList')
        
        # Get offer token (assuming first offer for simplicity, as per user snippet)
        subscriptionOfferDetails = details.getSubscriptionOfferDetails()
        if not subscriptionOfferDetails or subscriptionOfferDetails.isEmpty():
             print("BillingManager: No offer details found for subscription.")
             return
        
        # Taking the first offer token as in user snippet
        offerToken = subscriptionOfferDetails.get(0).getOfferToken()

        productDetailsParamsBuilder = ProductDetailsParams.newBuilder()
        productDetailsParamsBuilder.setProductDetails(details)
        productDetailsParamsBuilder.setOfferToken(offerToken)
        
        productDetailsParamsList = ArrayList()
        productDetailsParamsList.add(productDetailsParamsBuilder.build())

        flowParamsBuilder = BillingFlowParams.newBuilder()
        flowParamsBuilder.setProductDetailsParamsList(productDetailsParamsList)
        
        responseCode = self.billing_client.launchBillingFlow(self.activity, flowParamsBuilder.build()).getResponseCode()
        
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
            # BillingClient 5+: purchase.getProducts() returns List<String>
            products = purchase.getProducts() 
            if products and not products.isEmpty():
                sku = products.get(0)
            else:
                sku = "unknown"
                
            token = purchase.getPurchaseToken()
            order_id = purchase.getOrderId()
            if self.update_callback:
                self.update_callback(sku, token, order_id)
        
        Clock.schedule_once(callback_main, 0)
