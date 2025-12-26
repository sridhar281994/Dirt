from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
from weakref import ref

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.utils import platform


JoinSuccessCb = Callable[[str, int], None]
UserJoinedCb = Callable[[int], None]
UserOfflineCb = Callable[[int], None]
EndRequestedCb = Callable[[], None]


@dataclass
class AgoraJoinInfo:
    app_id: str
    channel: str
    token: str
    uid: int


class AgoraAndroidClient:
    """
    Thin Agora RTC wrapper for Kivy (Android) using PyJNIus.

    This manages:
    - RtcEngine lifecycle
    - join/leave channel
    - creating Android SurfaceViews for local + remote video and overlaying them
      on top of the Kivy window (FrameLayout added to PythonActivity)
    """

    def __init__(
        self,
        *,
        on_join_success: Optional[JoinSuccessCb] = None,
        on_user_joined: Optional[UserJoinedCb] = None,
        on_user_offline: Optional[UserOfflineCb] = None,
        on_end_requested: Optional[EndRequestedCb] = None,
    ):
        self._on_join_success = on_join_success
        self._on_user_joined = on_user_joined
        self._on_user_offline = on_user_offline
        self._on_end_requested = on_end_requested

        self._engine = None
        self._handler = None
        self._activity = None
        self._container = None
        self._remote_view = None
        self._local_view = None
        self._end_button = None
        self._end_click_listener = None
        self._joined = False

    @property
    def is_available(self) -> bool:
        return platform == "android"

    @property
    def is_joined(self) -> bool:
        return bool(self._joined)

    def _run_on_ui_thread(self, fn) -> None:
        if platform != "android":
            return
        try:
            from android.runnable import run_on_ui_thread  # type: ignore

            run_on_ui_thread(fn)()
        except Exception:
            # Fallback: best-effort invoke (may fail if not on UI thread).
            try:
                fn()
            except Exception:
                Logger.exception("AgoraAndroidClient: failed running UI operation")

    def ensure_engine(self, *, app_id: str) -> bool:
        if platform != "android":
            return False
        if self._engine is not None:
            return True

        try:
            from jnius import autoclass, PythonJavaClass, java_method  # type: ignore

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            context = activity.getApplicationContext()

            parent_ref = ref(self)

            class EventHandler(PythonJavaClass):  # type: ignore[misc]
                __javainterfaces__ = ["io/agora/rtc2/IRtcEngineEventHandler"]
                __javacontext__ = "app"

                @java_method("(Ljava/lang/String;II)V")
                def onJoinChannelSuccess(self, channel, uid, elapsed):  # noqa: N802
                    parent = parent_ref()
                    if not parent:
                        return
                    ch = str(channel) if channel is not None else ""
                    u = int(uid)

                    def _cb(*_):
                        parent._joined = True
                        if parent._on_join_success:
                            parent._on_join_success(ch, u)

                    Clock.schedule_once(_cb, 0)

                @java_method("(II)V")
                def onUserJoined(self, uid, elapsed):  # noqa: N802
                    parent = parent_ref()
                    if not parent:
                        return
                    u = int(uid)

                    def _cb(*_):
                        if parent._on_user_joined:
                            parent._on_user_joined(u)

                    Clock.schedule_once(_cb, 0)

                @java_method("(II)V")
                def onUserOffline(self, uid, reason):  # noqa: N802
                    parent = parent_ref()
                    if not parent:
                        return
                    u = int(uid)

                    def _cb(*_):
                        if parent._on_user_offline:
                            parent._on_user_offline(u)

                    Clock.schedule_once(_cb, 0)

            handler = EventHandler()

            RtcEngine = autoclass("io.agora.rtc2.RtcEngine")
            engine = None
            try:
                RtcEngineConfig = autoclass("io.agora.rtc2.RtcEngineConfig")
                config = RtcEngineConfig()
                # Field names follow Agora docs for v4.x
                config.mContext = context
                config.mAppId = str(app_id)
                config.mEventHandler = handler
                engine = RtcEngine.create(config)
            except Exception:
                # Older create signature fallback
                engine = RtcEngine.create(context, str(app_id), handler)

            Constants = autoclass("io.agora.rtc2.Constants")
            try:
                engine.setChannelProfile(int(Constants.CHANNEL_PROFILE_COMMUNICATION))
            except Exception:
                pass
            try:
                engine.enableVideo()
            except Exception:
                pass
            try:
                engine.enableAudio()
            except Exception:
                pass

            self._engine = engine
            self._handler = handler
            self._activity = activity
            return True
        except Exception:
            Logger.exception("AgoraAndroidClient: failed to initialize Agora engine")
            self._engine = None
            self._handler = None
            self._activity = None
            return False

    def _ensure_container(self) -> None:
        if platform != "android":
            return
        if self._container is not None:
            return

        from jnius import autoclass  # type: ignore

        FrameLayout = autoclass("android.widget.FrameLayout")
        ViewGroupLayoutParams = autoclass("android.view.ViewGroup$LayoutParams")
        activity = self._activity
        if activity is None:
            return

        def _create():
            try:
                container = FrameLayout(activity)
                container.setClickable(False)
                container.setFocusable(False)
                params = ViewGroupLayoutParams(
                    int(ViewGroupLayoutParams.MATCH_PARENT),
                    int(ViewGroupLayoutParams.MATCH_PARENT),
                )
                activity.addContentView(container, params)
                self._container = container
            except Exception:
                Logger.exception("AgoraAndroidClient: failed creating overlay container")

        self._run_on_ui_thread(_create)

    def _clear_views(self) -> None:
        if platform != "android":
            return
        container = self._container
        if container is None:
            self._remote_view = None
            self._local_view = None
            return

        def _clear():
            try:
                container.removeAllViews()
            except Exception:
                pass
            self._remote_view = None
            self._local_view = None
            self._end_button = None
            self._end_click_listener = None

        self._run_on_ui_thread(_clear)

    def _add_remote_view(self, *, uid: int) -> None:
        if platform != "android":
            return
        if self._engine is None:
            return

        from jnius import autoclass  # type: ignore

        RtcEngine = autoclass("io.agora.rtc2.RtcEngine")
        VideoCanvas = autoclass("io.agora.rtc2.video.VideoCanvas")
        FrameLayoutLayoutParams = autoclass("android.widget.FrameLayout$LayoutParams")
        Gravity = autoclass("android.view.Gravity")

        self._ensure_container()
        container = self._container
        if container is None:
            return

        def _add():
            try:
                # Fullscreen remote
                remote_view = RtcEngine.CreateRendererView(self._activity)
                # Kivy/SDL2 uses a SurfaceView; ensure Agora's surface is not hidden behind it.
                # This is critical to avoid only seeing the Kivy placeholder (avatar initials).
                try:
                    remote_view.setZOrderMediaOverlay(True)
                except Exception:
                    pass
                try:
                    # Some devices need OnTop for SurfaceView ordering.
                    remote_view.setZOrderOnTop(True)
                except Exception:
                    pass
                params = FrameLayoutLayoutParams(
                    int(FrameLayoutLayoutParams.MATCH_PARENT),
                    int(FrameLayoutLayoutParams.MATCH_PARENT),
                )
                params.gravity = int(Gravity.CENTER)
                container.addView(remote_view, params)
                if int(uid or 0) > 0:
                    self._engine.setupRemoteVideo(
                        VideoCanvas(remote_view, int(VideoCanvas.RENDER_MODE_HIDDEN), int(uid))
                    )
                self._remote_view = remote_view
            except Exception:
                Logger.exception("AgoraAndroidClient: failed adding remote view")

        self._run_on_ui_thread(_add)

    def _add_local_view(self, *, uid: int) -> None:
        if platform != "android":
            return
        if self._engine is None:
            return

        from jnius import autoclass  # type: ignore

        RtcEngine = autoclass("io.agora.rtc2.RtcEngine")
        VideoCanvas = autoclass("io.agora.rtc2.video.VideoCanvas")
        FrameLayoutLayoutParams = autoclass("android.widget.FrameLayout$LayoutParams")
        Gravity = autoclass("android.view.Gravity")

        self._ensure_container()
        container = self._container
        if container is None:
            return

        def _add():
            try:
                local_view = RtcEngine.CreateRendererView(self._activity)
                try:
                    local_view.setZOrderMediaOverlay(True)
                except Exception:
                    pass

                # Bottom-right PiP
                w = int(360)  # px; simple default (Kivy UI already scales)
                h = int(480)
                params = FrameLayoutLayoutParams(w, h)
                params.gravity = int(Gravity.BOTTOM) | int(Gravity.RIGHT)
                params.bottomMargin = int(30)
                params.rightMargin = int(30)
                container.addView(local_view, params)
                self._engine.setupLocalVideo(VideoCanvas(local_view, int(VideoCanvas.RENDER_MODE_HIDDEN), int(uid)))
                self._local_view = local_view
            except Exception:
                Logger.exception("AgoraAndroidClient: failed adding local view")

        self._run_on_ui_thread(_add)

    def _add_end_button(self) -> None:
        """
        Add a minimal native "End" control overlay so the user can leave the call
        even if the video overlay covers the Kivy UI.
        """
        if platform != "android":
            return
        container = self._container
        if container is None:
            return

        try:
            from jnius import autoclass, PythonJavaClass, java_method  # type: ignore

            Button = autoclass("android.widget.Button")
            FrameLayoutLayoutParams = autoclass("android.widget.FrameLayout$LayoutParams")
            Gravity = autoclass("android.view.Gravity")

            parent_ref = ref(self)

            class ClickListener(PythonJavaClass):  # type: ignore[misc]
                __javainterfaces__ = ["android/view/View$OnClickListener"]
                __javacontext__ = "app"

                @java_method("(Landroid/view/View;)V")
                def onClick(self, v):  # noqa: N802
                    parent = parent_ref()
                    if not parent:
                        return

                    def _cb(*_):
                        try:
                            parent.leave()
                        except Exception:
                            pass
                        try:
                            if parent._on_end_requested:
                                parent._on_end_requested()
                        except Exception:
                            pass

                    Clock.schedule_once(_cb, 0)

            listener = ClickListener()
            # Keep a strong reference so it doesn't get GC'd.
            self._end_click_listener = listener

            def _add():
                try:
                    btn = Button(self._activity)
                    btn.setText("End")
                    try:
                        btn.setAllCaps(False)
                    except Exception:
                        pass
                    btn.setOnClickListener(listener)
                    # Bottom-center
                    params = FrameLayoutLayoutParams(
                        int(FrameLayoutLayoutParams.WRAP_CONTENT),
                        int(FrameLayoutLayoutParams.WRAP_CONTENT),
                    )
                    params.gravity = int(Gravity.BOTTOM) | int(Gravity.CENTER_HORIZONTAL)
                    params.bottomMargin = int(40)
                    container.addView(btn, params)
                    self._end_button = btn
                except Exception:
                    Logger.exception("AgoraAndroidClient: failed adding end button")

            self._run_on_ui_thread(_add)
        except Exception:
            Logger.exception("AgoraAndroidClient: failed creating end button")

    def join(self, *, info: AgoraJoinInfo) -> bool:
        if platform != "android":
            return False
        if not self.ensure_engine(app_id=info.app_id):
            return False
        if self._engine is None:
            return False

        try:
            from jnius import autoclass  # type: ignore

            ChannelMediaOptions = autoclass("io.agora.rtc2.ChannelMediaOptions")
            opts = ChannelMediaOptions()
            # Best-effort fields (vary by SDK version)
            for k, v in (
                ("autoSubscribeAudio", True),
                ("autoSubscribeVideo", True),
                ("publishMicrophoneTrack", True),
                ("publishCameraTrack", True),
            ):
                try:
                    setattr(opts, k, v)
                except Exception:
                    pass

            # Views must exist before joining, otherwise first frames may be missed.
            self._clear_views()
            self._add_remote_view(uid=0)  # placeholder; will be re-setup on user join
            self._add_local_view(uid=int(info.uid))
            self._add_end_button()

            token = str(info.token or "")
            channel = str(info.channel or "")
            uid = int(info.uid or 0)
            self.set_last_local_uid(uid)

            ret = None
            try:
                ret = self._engine.joinChannel(token, channel, uid, opts)
            except Exception:
                # Some SDK builds expose joinChannel(token, channel, uid, options) vs (token, channel, info, uid)
                ret = self._engine.joinChannel(token, channel, "", uid, opts)

            Logger.info("AgoraAndroidClient: joinChannel ret=%s channel=%s uid=%s", ret, channel, uid)

            try:
                self._engine.startPreview()
            except Exception:
                pass

            return True
        except Exception:
            Logger.exception("AgoraAndroidClient: failed to join channel")
            return False

    def on_remote_user_joined(self, uid: int) -> None:
        # Replace placeholder setup with actual remote uid.
        if platform != "android":
            return
        if self._engine is None:
            return
        # Clear remote view and re-add to ensure canvas uses correct uid.
        # Keep local view (re-add after).
        local_uid = None
        try:
            local_uid = int(getattr(self, "_last_local_uid", 0) or 0)
        except Exception:
            local_uid = 0
        self._clear_views()
        self._add_remote_view(uid=int(uid))
        if local_uid:
            self._add_local_view(uid=local_uid)
        self._add_end_button()

    def set_last_local_uid(self, uid: int) -> None:
        self._last_local_uid = int(uid or 0)

    def mute_local_audio(self, mute: bool) -> None:
        if platform != "android":
            return
        if self._engine is None:
            return
        try:
            self._engine.muteLocalAudioStream(bool(mute))
        except Exception:
            pass

    def switch_camera(self) -> None:
        if platform != "android":
            return
        if self._engine is None:
            return
        try:
            self._engine.switchCamera()
        except Exception:
            pass

    def leave(self) -> None:
        if platform != "android":
            return
        if self._engine is None:
            return
        try:
            self._engine.leaveChannel()
        except Exception:
            pass
        self._joined = False
        self._clear_views()

    def destroy(self) -> None:
        if platform != "android":
            return
        try:
            self.leave()
        except Exception:
            pass

        # Remove overlay container from the view hierarchy (important so it doesn't
        # keep intercepting input / consuming resources).
        container = self._container
        if container is not None:
            def _rm():
                try:
                    parent = container.getParent()
                    if parent is not None:
                        parent.removeView(container)
                except Exception:
                    pass

            self._run_on_ui_thread(_rm)
        try:
            from jnius import autoclass  # type: ignore

            RtcEngine = autoclass("io.agora.rtc2.RtcEngine")
            try:
                RtcEngine.destroy()
            except Exception:
                pass
        except Exception:
            pass
        self._engine = None
        self._handler = None
        self._activity = None
        self._container = None

