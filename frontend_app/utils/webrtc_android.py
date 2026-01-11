from __future__ import annotations

import json
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Thread
from typing import Any, Callable, Dict, Optional
from weakref import ref

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.metrics import dp
from kivy.utils import platform

from frontend_app.utils.storage import get_token
from frontend_app.utils.api import (
    api_webrtc_get_answer,
    api_webrtc_get_ice,
    api_webrtc_get_offer,
    api_webrtc_post_answer,
    api_webrtc_post_ice,
    api_webrtc_post_offer,
)


ConnectedCb = Callable[[], None]
DisconnectedCb = Callable[[], None]
EndRequestedCb = Callable[[], None]


@dataclass
class WebRTCJoinInfo:
    session_id: int
    role: str  # offerer|answerer
    stun_urls: list[str]
    ice_servers: list[dict] | None = None
    ws_url: str | None = None


class WebRTCAndroidClient:
    """
    Minimal WebRTC (org.webrtc) client for Kivy/Android using PyJNIus.

    Rendering:
    - Uses Android native views added to PythonActivity via addContentView.
    - Intentionally leaves top/bottom margins so Kivy UI remains visible.

    Signaling:
    - HTTP polling via backend endpoints (/api/video/webrtc/*).
    - If ws_url is provided (and websocket-client is installed), uses WebSocket signaling.
    """

    def __init__(
        self,
        *,
        on_connected: Optional[ConnectedCb] = None,
        on_disconnected: Optional[DisconnectedCb] = None,
        on_end_requested: Optional[EndRequestedCb] = None,
    ):
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_end_requested = on_end_requested

        self._activity = None
        self._container = None
        self._remote_view = None
        self._local_view = None
        self._end_button = None
        self._end_click_listener = None

        self._egl_base = None
        self._factory = None
        self._pc = None
        self._video_capturer = None
        self._video_source = None
        self._audio_source = None
        self._local_video_track = None
        self._local_audio_track = None
        self._local_stream = None

        self._session_id: int = 0
        self._role: str = ""
        self._stop_ev = Event()
        self._signaling_thread: Thread | None = None
        self._ice_since_id: int = 0
        self._connected_fired = False
        self._is_muted = False
        self._prefer_front = False
        self._ws = None
        self._ws_thread: Thread | None = None
        self._ws_recv_q: Queue[dict] = Queue()
        self._ws_enabled: bool = False
        self._ws_url: str = ""
        self._token: str = ""

    @property
    def is_available(self) -> bool:
        return platform == "android"

    @property
    def is_running(self) -> bool:
        return bool(self._pc is not None and not self._stop_ev.is_set())

    def _run_on_ui_thread(self, fn) -> None:
        if platform != "android":
            return
        try:
            from android.runnable import run_on_ui_thread  # type: ignore

            run_on_ui_thread(fn)()
        except Exception:
            try:
                fn()
            except Exception:
                Logger.exception("WebRTCAndroidClient: failed running UI operation")

    def _px(self, value_dp: float) -> int:
        # Convert dp to px on Android; fall back to Kivy dp if we can't query density.
        if platform != "android":
            return int(value_dp)
        try:
            from jnius import autoclass  # type: ignore

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            act = PythonActivity.mActivity
            metrics = act.getResources().getDisplayMetrics()
            density = float(metrics.density or 1.0)
            return int(float(value_dp) * density)
        except Exception:
            return int(dp(value_dp))

    def _ensure_activity(self) -> bool:
        if platform != "android":
            return False
        if self._activity is not None:
            return True
        try:
            from jnius import autoclass  # type: ignore

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            self._activity = PythonActivity.mActivity
            return self._activity is not None
        except Exception:
            Logger.exception("WebRTCAndroidClient: failed to get activity")
            self._activity = None
            return False

    def _ensure_container(self) -> None:
        if platform != "android":
            return
        if self._container is not None:
            return
        if not self._ensure_activity():
            return
        try:
            from jnius import autoclass  # type: ignore

            FrameLayout = autoclass("android.widget.FrameLayout")
            ViewGroupLayoutParams = autoclass("android.view.ViewGroup$LayoutParams")
            activity = self._activity

            def _create():
                try:
                    container = FrameLayout(activity)
                    # Don't eat touches; let Kivy UI remain interactive.
                    try:
                        container.setClickable(False)
                        container.setFocusable(False)
                    except Exception:
                        pass
                    params = ViewGroupLayoutParams(
                        int(ViewGroupLayoutParams.MATCH_PARENT),
                        int(ViewGroupLayoutParams.MATCH_PARENT),
                    )
                    activity.addContentView(container, params)
                    self._container = container
                except Exception:
                    Logger.exception("WebRTCAndroidClient: failed creating overlay container")

            self._run_on_ui_thread(_create)
        except Exception:
            Logger.exception("WebRTCAndroidClient: failed preparing container")

    def _clear_views(self) -> None:
        if platform != "android":
            return
        container = self._container
        if container is None:
            self._remote_view = None
            self._local_view = None
            self._end_button = None
            self._end_click_listener = None
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

    def _remove_container(self) -> None:
        if platform != "android":
            return
        container = self._container
        if container is None:
            return

        def _rm():
            try:
                parent = container.getParent()
                if parent is not None:
                    parent.removeView(container)
            except Exception:
                pass

        self._run_on_ui_thread(_rm)
        self._container = None

    def _ensure_webrtc_factory(self) -> bool:
        if platform != "android":
            return False
        if self._factory is not None and self._egl_base is not None:
            return True
        if not self._ensure_activity():
            return False
        try:
            from jnius import autoclass  # type: ignore

            PeerConnectionFactory = autoclass("org.webrtc.PeerConnectionFactory")
            InitializationOptions = autoclass("org.webrtc.PeerConnectionFactory$InitializationOptions")
            EglBase = autoclass("org.webrtc.EglBase")
            DefaultVideoEncoderFactory = autoclass("org.webrtc.DefaultVideoEncoderFactory")
            DefaultVideoDecoderFactory = autoclass("org.webrtc.DefaultVideoDecoderFactory")

            context = self._activity.getApplicationContext()
            init_opts = InitializationOptions.builder(context).createInitializationOptions()
            PeerConnectionFactory.initialize(init_opts)

            egl_base = EglBase.create()
            egl_ctx = egl_base.getEglBaseContext()
            enc = DefaultVideoEncoderFactory(egl_ctx, True, True)
            dec = DefaultVideoDecoderFactory(egl_ctx)
            factory = (
                PeerConnectionFactory.builder()
                .setVideoEncoderFactory(enc)
                .setVideoDecoderFactory(dec)
                .createPeerConnectionFactory()
            )
            self._egl_base = egl_base
            self._factory = factory
            return True
        except Exception:
            Logger.exception("WebRTCAndroidClient: failed to init PeerConnectionFactory")
            self._egl_base = None
            self._factory = None
            return False

    def _create_renderers(self) -> None:
        if platform != "android":
            return
        if self._container is None or self._egl_base is None:
            return
        try:
            from jnius import autoclass  # type: ignore

            TextureViewRenderer = autoclass("org.webrtc.TextureViewRenderer")
            FrameLayoutLayoutParams = autoclass("android.widget.FrameLayout$LayoutParams")
            Gravity = autoclass("android.view.Gravity")
            RendererCommonScalingType = autoclass("org.webrtc.RendererCommon$ScalingType")

            egl_ctx = self._egl_base.getEglBaseContext()
            activity = self._activity
            container = self._container

            # Fullscreen video:
            # Previously we left top/bottom margins so Kivy UI stayed visible.
            # Users want true fullscreen rendering, so margins are disabled.
            top_margin = 0
            bot_margin = 0

            def _add():
                try:
                    remote = TextureViewRenderer(activity)
                    remote.init(egl_ctx, None)
                    try:
                        remote.setScalingType(RendererCommonScalingType.SCALE_ASPECT_FILL)
                    except Exception:
                        pass
                    try:
                        remote.setClickable(False)
                        remote.setFocusable(False)
                    except Exception:
                        pass
                    params = FrameLayoutLayoutParams(
                        int(FrameLayoutLayoutParams.MATCH_PARENT),
                        int(FrameLayoutLayoutParams.MATCH_PARENT),
                    )
                    params.gravity = int(Gravity.CENTER)
                    try:
                        params.topMargin = int(top_margin)
                        params.bottomMargin = int(bot_margin)
                    except Exception:
                        pass
                    container.addView(remote, params)
                    self._remote_view = remote

                    local = TextureViewRenderer(activity)
                    local.init(egl_ctx, None)
                    # Native upside-down fix:
                    # On some Android devices, the camera frames are presented inverted
                    # (180°) in the local TextureViewRenderer. Rotating the View itself
                    # corrects the preview without changing the capture pipeline.
                    try:
                        local.setRotation(180.0)
                    except Exception:
                        pass
                    try:
                        local.setScalingType(RendererCommonScalingType.SCALE_ASPECT_FILL)
                    except Exception:
                        pass
                    try:
                        local.setZOrderMediaOverlay(True)
                    except Exception:
                        pass
                    try:
                        local.setClickable(False)
                        local.setFocusable(False)
                    except Exception:
                        pass
                    w = self._px(110)
                    h = self._px(150)
                    lp = FrameLayoutLayoutParams(int(w), int(h))
                    lp.gravity = int(Gravity.BOTTOM) | int(Gravity.RIGHT)
                    lp.bottomMargin = int(self._px(10))
                    lp.rightMargin = int(self._px(10))
                    container.addView(local, lp)
                    self._local_view = local
                except Exception:
                    Logger.exception("WebRTCAndroidClient: failed adding renderers")

            self._run_on_ui_thread(_add)
        except Exception:
            Logger.exception("WebRTCAndroidClient: failed creating renderers")

    def _add_native_end_button(self) -> None:
        if platform != "android":
            return
        if self._container is None:
            return
        try:
            from jnius import PythonJavaClass, autoclass, java_method  # type: ignore

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
                            parent.stop()
                        except Exception:
                            pass
                        try:
                            if parent._on_end_requested:
                                parent._on_end_requested()
                        except Exception:
                            pass

                    Clock.schedule_once(_cb, 0)

            listener = ClickListener()
            self._end_click_listener = listener

            # Fullscreen video: no reserved margin for Kivy controls.
            bot_margin = 0

            def _add():
                try:
                    btn = Button(self._activity)
                    btn.setText("End")
                    try:
                        btn.setAllCaps(False)
                    except Exception:
                        pass
                    btn.setOnClickListener(listener)
                    params = FrameLayoutLayoutParams(
                        int(FrameLayoutLayoutParams.WRAP_CONTENT),
                        int(FrameLayoutLayoutParams.WRAP_CONTENT),
                    )
                    params.gravity = int(Gravity.BOTTOM) | int(Gravity.CENTER_HORIZONTAL)
                    params.bottomMargin = int(bot_margin + self._px(10))
                    self._container.addView(btn, params)
                    self._end_button = btn
                except Exception:
                    Logger.exception("WebRTCAndroidClient: failed adding End button")

            self._run_on_ui_thread(_add)
        except Exception:
            Logger.exception("WebRTCAndroidClient: failed creating End button")

    def _create_peer_connection(self, *, stun_urls: list[str], ice_servers: list[dict] | None = None) -> bool:
        if platform != "android":
            return False
        if not self._ensure_webrtc_factory():
            return False
        if self._factory is None:
            return False
        try:
            from jnius import PythonJavaClass, autoclass, java_method  # type: ignore

            PeerConnection = autoclass("org.webrtc.PeerConnection")
            PeerConnectionRTCConfiguration = autoclass("org.webrtc.PeerConnection$RTCConfiguration")
            IceServer = autoclass("org.webrtc.PeerConnection$IceServer")
            ArrayList = autoclass("java.util.ArrayList")

            parent_ref = ref(self)

            class PCObserver(PythonJavaClass):  # type: ignore[misc]
                __javainterfaces__ = ["org/webrtc/PeerConnection$Observer"]
                __javacontext__ = "app"

                @java_method("(Lorg/webrtc/IceCandidate;)V")
                def onIceCandidate(self, candidate):  # noqa: N802
                    parent = parent_ref()
                    if not parent:
                        return
                    try:
                        payload = {
                            "sdpMid": str(candidate.sdpMid),
                            "sdpMLineIndex": int(candidate.sdpMLineIndex),
                            "candidate": str(candidate.sdp),
                        }
                        parent._post_ice(payload)
                    except Exception:
                        pass

                @java_method("(Lorg/webrtc/MediaStream;)V")
                def onAddStream(self, stream):  # noqa: N802
                    parent = parent_ref()
                    if not parent:
                        return
                    try:
                        # First remote video track -> attach sink.
                        tracks = stream.videoTracks
                        if tracks is None or tracks.size() <= 0:
                            return
                        vt = tracks.get(0)

                        def _attach(*_):
                            try:
                                if parent._remote_view is not None:
                                    vt.addSink(parent._remote_view)
                                    parent._fire_connected()
                            except Exception:
                                pass

                        Clock.schedule_once(_attach, 0)
                    except Exception:
                        pass

                @java_method("(Lorg/webrtc/PeerConnection$IceConnectionState;)V")
                def onIceConnectionChange(self, state):  # noqa: N802
                    parent = parent_ref()
                    if not parent:
                        return
                    # Best-effort: fire "connected" once when we get any connected-ish state.
                    try:
                        s = str(state.toString() if state is not None else "")
                    except Exception:
                        s = ""
                    if s in {"CONNECTED", "COMPLETED"}:
                        Clock.schedule_once(lambda *_: parent._fire_connected(), 0)

                # Required interface methods (no-ops)
                @java_method("(Lorg/webrtc/PeerConnection$SignalingState;)V")
                def onSignalingChange(self, state):  # noqa: N802
                    return

                @java_method("(Lorg/webrtc/PeerConnection$IceGatheringState;)V")
                def onIceGatheringChange(self, state):  # noqa: N802
                    return

                @java_method("(Z)V")
                def onIceConnectionReceivingChange(self, receiving):  # noqa: N802
                    return

                @java_method("([Lorg/webrtc/IceCandidate;)V")
                def onIceCandidatesRemoved(self, candidates):  # noqa: N802
                    return

                @java_method("(Lorg/webrtc/MediaStream;)V")
                def onRemoveStream(self, stream):  # noqa: N802
                    return

                @java_method("(Lorg/webrtc/RtpReceiver;[Lorg/webrtc/MediaStream;)V")
                def onAddTrack(self, receiver, mediaStreams):  # noqa: N802
                    return

                # Some WebRTC versions prefer Unified Plan callback.
                @java_method("(Lorg/webrtc/RtpTransceiver;)V")
                def onTrack(self, transceiver):  # noqa: N802
                    return

                @java_method("(Lorg/webrtc/DataChannel;)V")
                def onDataChannel(self, channel):  # noqa: N802
                    return

                @java_method("()V")
                def onRenegotiationNeeded(self):  # noqa: N802
                    return

            servers = ArrayList()

            def _add_server(url: str, username: str | None = None, credential: str | None = None) -> None:
                try:
                    b = IceServer.builder(str(url))
                    if username:
                        try:
                            b = b.setUsername(str(username))
                        except Exception:
                            pass
                    if credential:
                        try:
                            b = b.setPassword(str(credential))
                        except Exception:
                            pass
                    servers.add(b.createIceServer())
                except Exception:
                    return

            if ice_servers and isinstance(ice_servers, list):
                for s in ice_servers:
                    if not isinstance(s, dict):
                        continue
                    urls = s.get("urls")
                    username = s.get("username")
                    credential = s.get("credential")
                    if isinstance(urls, str) and urls.strip():
                        _add_server(urls.strip(), username=username, credential=credential)
                    elif isinstance(urls, list):
                        for u in urls:
                            if isinstance(u, str) and u.strip():
                                _add_server(u.strip(), username=username, credential=credential)

            # Fallback: STUN-only
            if servers.size() <= 0:
                for url in (stun_urls or []):
                    if not url:
                        continue
                    _add_server(str(url))

            rtc_config = PeerConnectionRTCConfiguration(servers)
            observer = PCObserver()
            pc = self._factory.createPeerConnection(rtc_config, observer)
            self._pc = pc
            self._pc_observer = observer  # keep strong ref
            return pc is not None
        except Exception:
            Logger.exception("WebRTCAndroidClient: failed creating PeerConnection")
            self._pc = None
            return False

    def _start_local_media(self, *, prefer_front: bool) -> None:
        if platform != "android":
            return
        if self._factory is None or self._pc is None:
            return
        try:
            from jnius import autoclass  # type: ignore

            MediaConstraints = autoclass("org.webrtc.MediaConstraints")
            Camera2Enumerator = autoclass("org.webrtc.Camera2Enumerator")
            SurfaceTextureHelper = autoclass("org.webrtc.SurfaceTextureHelper")

            context = self._activity.getApplicationContext()
            enumerator = Camera2Enumerator(context)
            device_names = enumerator.getDeviceNames()
            chosen = None
            for i in range(device_names.size()):
                name = device_names.get(i)
                if bool(prefer_front) and bool(enumerator.isFrontFacing(name)):
                    chosen = name
                    break
                if (not bool(prefer_front)) and bool(enumerator.isBackFacing(name)):
                    chosen = name
                    break
            if chosen is None and device_names.size() > 0:
                chosen = device_names.get(0)

            capturer = enumerator.createCapturer(chosen, None)
            self._video_capturer = capturer

            egl_ctx = self._egl_base.getEglBaseContext()
            sth = SurfaceTextureHelper.create("CaptureThread", egl_ctx)

            video_source = self._factory.createVideoSource(False)
            self._video_source = video_source
            capturer.initialize(sth, context, video_source.getCapturerObserver())
            try:
                capturer.startCapture(640, 360, 15)
            except Exception:
                # Some devices prefer 720p; try once.
                try:
                    capturer.startCapture(1280, 720, 15)
                except Exception:
                    pass

            vt = self._factory.createVideoTrack("local_video", video_source)
            self._local_video_track = vt

            audio_source = self._factory.createAudioSource(MediaConstraints())
            self._audio_source = audio_source
            at = self._factory.createAudioTrack("local_audio", audio_source)
            self._local_audio_track = at
            try:
                at.setEnabled(not bool(self._is_muted))
            except Exception:
                pass

            stream = self._factory.createLocalMediaStream("local_stream")
            stream.addTrack(vt)
            stream.addTrack(at)
            self._local_stream = stream
            try:
                self._pc.addStream(stream)
            except Exception:
                pass

            # Attach local preview sink.
            if self._local_view is not None:
                try:
                    self._local_view.setMirror(bool(prefer_front))
                except Exception:
                    pass
                try:
                    vt.addSink(self._local_view)
                except Exception:
                    pass
        except Exception:
            Logger.exception("WebRTCAndroidClient: failed starting local media")

    def _sdp_set_local_then(self, desc, *, after_set: Callable[[], None]) -> None:
        """
        setLocalDescription(desc) then run after_set() on success.
        """
        try:
            from jnius import PythonJavaClass, java_method  # type: ignore

            parent_ref = ref(self)

            class _S(PythonJavaClass):  # type: ignore[misc]
                __javainterfaces__ = ["org/webrtc/SdpObserver"]
                __javacontext__ = "app"

                @java_method("(Lorg/webrtc/SessionDescription;)V")
                def onCreateSuccess(self, _d):  # noqa: N802
                    return

                @java_method("(Ljava/lang/String;)V")
                def onCreateFailure(self, _s):  # noqa: N802
                    return

                @java_method("()V")
                def onSetSuccess(self):  # noqa: N802
                    parent = parent_ref()
                    if not parent:
                        return
                    Clock.schedule_once(lambda *_: after_set(), 0)

                @java_method("(Ljava/lang/String;)V")
                def onSetFailure(self, _s):  # noqa: N802
                    return

            obs = _S()
            self._pc.setLocalDescription(obs, desc)
        except Exception:
            Logger.exception("WebRTCAndroidClient: setLocalDescription failed")

    def _post_ice(self, candidate_payload: Dict[str, Any]) -> None:
        sid = int(self._session_id or 0)
        if sid <= 0 or self._stop_ev.is_set():
            return
        try:
            if self._ws_enabled:
                self._ws_send(kind="ice", payload=candidate_payload or {})
            else:
                api_webrtc_post_ice(session_id=sid, candidate=candidate_payload or {})
        except Exception:
            # signaling errors are best-effort; polling will eventually recover
            return

    def _fire_connected(self) -> None:
        if self._connected_fired:
            return
        self._connected_fired = True
        if self._on_connected:
            try:
                self._on_connected()
            except Exception:
                pass

    def start(self, *, info: WebRTCJoinInfo, prefer_front_camera: bool = False) -> bool:
        if platform != "android":
            return False
        self.stop()
        if not self._ensure_activity():
            return False
        if not self._ensure_webrtc_factory():
            return False

        self._session_id = int(info.session_id or 0)
        self._role = str(info.role or "").strip().lower()
        self._ice_since_id = 0
        self._connected_fired = False
        self._stop_ev.clear()
        self._prefer_front = bool(prefer_front_camera)
        self._ws_enabled = False
        self._ws_url = str(info.ws_url or "").strip()
        self._token = str(get_token() or "").strip()

        self._ensure_container()
        self._clear_views()
        self._create_renderers()
        self._add_native_end_button()

        ok = self._create_peer_connection(stun_urls=list(info.stun_urls or []), ice_servers=info.ice_servers)
        if not ok:
            return False
        self._start_local_media(prefer_front=bool(prefer_front_camera))

        # Start signaling worker in background.
        self._signaling_thread = Thread(target=self._run_signaling_loop, daemon=True)
        self._signaling_thread.start()
        return True

    def _ws_connect(self) -> None:
        if not self._ws_url or not self._token or self._stop_ev.is_set():
            return
        try:
            import websocket  # type: ignore
        except Exception:
            return

        url = self._ws_url
        if "?" in url:
            url = f"{url}&token={self._token}"
        else:
            url = f"{url}?token={self._token}"

        parent_ref = ref(self)

        def on_message(_ws, message):
            parent = parent_ref()
            if not parent or parent._stop_ev.is_set():
                return
            try:
                obj = json.loads(message or "{}")
            except Exception:
                return
            if not isinstance(obj, dict):
                return
            if str(obj.get("type") or "").strip().lower() != "signal":
                return
            try:
                if int(obj.get("call_id") or 0) != int(parent._session_id or 0):
                    return
            except Exception:
                return
            try:
                parent._ws_recv_q.put_nowait(obj)
            except Exception:
                return

        def on_open(_ws):
            parent = parent_ref()
            if parent:
                parent._ws_enabled = True

        def on_close(_ws, *_args):
            parent = parent_ref()
            if parent:
                parent._ws_enabled = False

        def on_error(_ws, _err):
            parent = parent_ref()
            if parent:
                parent._ws_enabled = False

        try:
            ws_app = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message, on_close=on_close, on_error=on_error)
        except Exception:
            return

        self._ws = ws_app

        def _run():
            try:
                ws_app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            finally:
                parent = parent_ref()
                if parent:
                    parent._ws_enabled = False

        self._ws_thread = Thread(target=_run, daemon=True)
        self._ws_thread.start()

    def _ws_send(self, *, kind: str, payload: dict) -> None:
        if not self._ws_enabled or self._ws is None or self._stop_ev.is_set():
            return
        try:
            msg = {
                "type": "signal",
                "call_id": int(self._session_id or 0),
                "kind": str(kind),
                "payload": payload or {},
            }
            self._ws.send(json.dumps(msg))
        except Exception:
            self._ws_enabled = False

    def _run_signaling_loop(self) -> None:
        sid = int(self._session_id or 0)
        if sid <= 0:
            return
        role = (self._role or "").strip().lower()

        # Try websocket signaling first (best-effort).
        try:
            self._ws_connect()
        except Exception:
            pass

        # Offerer creates offer immediately.
        if role == "offerer":
            self._create_and_send_offer()

        # Answerer waits for offer then answers.
        if role == "answerer":
            self._wait_offer_then_answer()

        # Both sides: websocket receive loop (fallback to polling if WS unavailable).
        while not self._stop_ev.is_set() and self._pc is not None:
            try:
                if self._ws_enabled:
                    try:
                        msg = self._ws_recv_q.get(timeout=0.35)
                    except Empty:
                        msg = None
                    if isinstance(msg, dict):
                        self._handle_ws_signal(msg)
                else:
                    if role == "offerer":
                        self._poll_answer_once()
                    self._poll_ice_once()
            except Exception:
                pass
            if not self._ws_enabled:
                self._stop_ev.wait(0.35)

    def _handle_ws_signal(self, msg: dict) -> None:
        kind = str(msg.get("kind") or "").strip().lower()
        payload = msg.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if kind == "offer":
            if (self._role or "").strip().lower() == "answerer":
                sdp = str(payload.get("sdp") or "")
                if sdp:
                    self._set_remote_offer_and_answer(sdp)
        elif kind == "answer":
            if (self._role or "").strip().lower() == "offerer":
                sdp = str(payload.get("sdp") or "")
                if sdp:
                    self._set_remote_answer(sdp)
        elif kind == "ice":
            self._add_ice_candidate(payload)
        elif kind == "bye":
            Clock.schedule_once(lambda *_: self.stop(), 0)

    def _create_and_send_offer(self) -> None:
        if self._pc is None or self._stop_ev.is_set():
            return
        try:
            from jnius import PythonJavaClass, autoclass, java_method  # type: ignore

            MediaConstraints = autoclass("org.webrtc.MediaConstraints")

            parent_ref = ref(self)

            class _Obs(PythonJavaClass):  # type: ignore[misc]
                __javainterfaces__ = ["org/webrtc/SdpObserver"]
                __javacontext__ = "app"

                @java_method("(Lorg/webrtc/SessionDescription;)V")
                def onCreateSuccess(self, desc):  # noqa: N802
                    parent = parent_ref()
                    if not parent or parent._stop_ev.is_set() or parent._pc is None:
                        return

                    def _after_set():
                        try:
                            if parent._ws_enabled:
                                parent._ws_send(kind="offer", payload={"type": "offer", "sdp": str(desc.description or "")})
                            else:
                                api_webrtc_post_offer(
                                    session_id=int(parent._session_id),
                                    payload={"type": "offer", "sdp": str(desc.description or "")},
                                )
                        except Exception:
                            pass

                    parent._sdp_set_local_then(desc, after_set=_after_set)

                @java_method("(Ljava/lang/String;)V")
                def onCreateFailure(self, _s):  # noqa: N802
                    return

                @java_method("()V")
                def onSetSuccess(self):  # noqa: N802
                    return

                @java_method("(Ljava/lang/String;)V")
                def onSetFailure(self, _s):  # noqa: N802
                    return

            self._pc.createOffer(_Obs(), MediaConstraints())
        except Exception:
            Logger.exception("WebRTCAndroidClient: createOffer failed")

    def _wait_offer_then_answer(self) -> None:
        # Poll until we have an offer (or stop requested).
        while not self._stop_ev.is_set() and self._pc is not None:
            if self._ws_enabled:
                try:
                    msg = self._ws_recv_q.get(timeout=0.35)
                except Empty:
                    msg = None
                if isinstance(msg, dict) and str(msg.get("kind") or "").strip().lower() == "offer":
                    payload = msg.get("payload") or {}
                    if isinstance(payload, dict):
                        sdp = str(payload.get("sdp") or "")
                        if sdp:
                            self._set_remote_offer_and_answer(sdp)
                            return
            try:
                data = api_webrtc_get_offer(session_id=int(self._session_id))
                offer = data.get("offer")
                if offer and isinstance(offer, dict):
                    payload = offer.get("payload") or {}
                    sdp = str(payload.get("sdp") or "")
                    if sdp:
                        self._set_remote_offer_and_answer(sdp)
                        return
            except Exception:
                pass
            self._stop_ev.wait(0.35)

    def _set_remote_offer_and_answer(self, sdp: str) -> None:
        if self._pc is None or self._stop_ev.is_set():
            return
        try:
            from jnius import PythonJavaClass, autoclass, java_method  # type: ignore

            SessionDescription = autoclass("org.webrtc.SessionDescription")
            SessionDescriptionType = autoclass("org.webrtc.SessionDescription$Type")
            MediaConstraints = autoclass("org.webrtc.MediaConstraints")

            parent_ref = ref(self)

            class _SetObs(PythonJavaClass):  # type: ignore[misc]
                __javainterfaces__ = ["org/webrtc/SdpObserver"]
                __javacontext__ = "app"

                @java_method("()V")
                def onSetSuccess(self):  # noqa: N802
                    parent = parent_ref()
                    if not parent or parent._pc is None or parent._stop_ev.is_set():
                        return
                    parent._create_and_send_answer()

                @java_method("(Ljava/lang/String;)V")
                def onSetFailure(self, _s):  # noqa: N802
                    return

                @java_method("(Lorg/webrtc/SessionDescription;)V")
                def onCreateSuccess(self, _d):  # noqa: N802
                    return

                @java_method("(Ljava/lang/String;)V")
                def onCreateFailure(self, _s):  # noqa: N802
                    return

            desc = SessionDescription(SessionDescriptionType.OFFER, str(sdp))
            self._pc.setRemoteDescription(_SetObs(), desc)
        except Exception:
            Logger.exception("WebRTCAndroidClient: setRemote(offer) failed")

    def _create_and_send_answer(self) -> None:
        if self._pc is None or self._stop_ev.is_set():
            return
        try:
            from jnius import PythonJavaClass, autoclass, java_method  # type: ignore

            MediaConstraints = autoclass("org.webrtc.MediaConstraints")

            parent_ref = ref(self)

            class _Obs(PythonJavaClass):  # type: ignore[misc]
                __javainterfaces__ = ["org/webrtc/SdpObserver"]
                __javacontext__ = "app"

                @java_method("(Lorg/webrtc/SessionDescription;)V")
                def onCreateSuccess(self, desc):  # noqa: N802
                    parent = parent_ref()
                    if not parent or parent._pc is None or parent._stop_ev.is_set():
                        return

                    def _after_set():
                        try:
                            if parent._ws_enabled:
                                parent._ws_send(kind="answer", payload={"type": "answer", "sdp": str(desc.description or "")})
                            else:
                                api_webrtc_post_answer(
                                    session_id=int(parent._session_id),
                                    payload={"type": "answer", "sdp": str(desc.description or "")},
                                )
                        except Exception:
                            pass

                    parent._sdp_set_local_then(desc, after_set=_after_set)

                @java_method("(Ljava/lang/String;)V")
                def onCreateFailure(self, _s):  # noqa: N802
                    return

                @java_method("()V")
                def onSetSuccess(self):  # noqa: N802
                    return

                @java_method("(Ljava/lang/String;)V")
                def onSetFailure(self, _s):  # noqa: N802
                    return

            self._pc.createAnswer(_Obs(), MediaConstraints())
        except Exception:
            Logger.exception("WebRTCAndroidClient: createAnswer failed")

    def _poll_answer_once(self) -> None:
        if self._pc is None or self._stop_ev.is_set():
            return
        try:
            data = api_webrtc_get_answer(session_id=int(self._session_id))
            ans = data.get("answer")
            if not ans:
                return
            payload = ans.get("payload") or {}
            sdp = str(payload.get("sdp") or "")
            if not sdp:
                return
            self._set_remote_answer(sdp)
        except Exception:
            return

    def _set_remote_answer(self, sdp: str) -> None:
        if self._pc is None or self._stop_ev.is_set():
            return
        try:
            from jnius import PythonJavaClass, autoclass, java_method  # type: ignore

            SessionDescription = autoclass("org.webrtc.SessionDescription")
            SessionDescriptionType = autoclass("org.webrtc.SessionDescription$Type")

            parent_ref = ref(self)

            class _Obs(PythonJavaClass):  # type: ignore[misc]
                __javainterfaces__ = ["org/webrtc/SdpObserver"]
                __javacontext__ = "app"

                @java_method("()V")
                def onSetSuccess(self):  # noqa: N802
                    parent = parent_ref()
                    if parent:
                        Clock.schedule_once(lambda *_: parent._fire_connected(), 0)

                @java_method("(Ljava/lang/String;)V")
                def onSetFailure(self, _s):  # noqa: N802
                    return

                @java_method("(Lorg/webrtc/SessionDescription;)V")
                def onCreateSuccess(self, _d):  # noqa: N802
                    return

                @java_method("(Ljava/lang/String;)V")
                def onCreateFailure(self, _s):  # noqa: N802
                    return

            desc = SessionDescription(SessionDescriptionType.ANSWER, str(sdp))
            self._pc.setRemoteDescription(_Obs(), desc)
        except Exception:
            Logger.exception("WebRTCAndroidClient: setRemote(answer) failed")

    def _poll_ice_once(self) -> None:
        if self._pc is None or self._stop_ev.is_set():
            return
        try:
            data = api_webrtc_get_ice(session_id=int(self._session_id), since_id=int(self._ice_since_id), limit=50)
            items = data.get("candidates") or []
        except Exception:
            return
        if not isinstance(items, list) or not items:
            return
        for it in items:
            try:
                cid = int(it.get("id") or 0)
            except Exception:
                cid = 0
            payload = it.get("payload") or {}
            if cid > self._ice_since_id:
                self._ice_since_id = cid
            self._add_ice_candidate(payload)

    def _add_ice_candidate(self, payload: Dict[str, Any]) -> None:
        if self._pc is None or self._stop_ev.is_set():
            return
        try:
            from jnius import autoclass  # type: ignore

            IceCandidate = autoclass("org.webrtc.IceCandidate")
            mid = str(payload.get("sdpMid") or "")
            try:
                mline = int(payload.get("sdpMLineIndex") or 0)
            except Exception:
                mline = 0
            cand = str(payload.get("candidate") or "")
            if not cand:
                return
            ic = IceCandidate(mid, int(mline), cand)
            self._pc.addIceCandidate(ic)
        except Exception:
            # Ignore malformed candidates.
            return

    def set_muted(self, muted: bool) -> None:
        self._is_muted = bool(muted)
        try:
            if self._local_audio_track is not None:
                self._local_audio_track.setEnabled(not bool(muted))
        except Exception:
            pass

    def switch_camera(self) -> None:
        if platform != "android":
            return
        cap = self._video_capturer
        if cap is None:
            return
        try:
            # CameraVideoCapturer.switchCamera(handler)
            cap.switchCamera(None)
            self._prefer_front = not bool(self._prefer_front)
            self.set_local_mirror(bool(self._prefer_front))
        except Exception:
            pass

    def set_local_mirror(self, mirror: bool) -> None:
        if platform != "android":
            return
        try:
            if self._local_view is not None:
                self._local_view.setMirror(bool(mirror))
        except Exception:
            pass

    def stop(self) -> None:
        self._stop_ev.set()
        try:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass
        except Exception:
            pass
        self._ws = None
        self._ws_enabled = False
        try:
            if self._pc is not None:
                try:
                    self._pc.close()
                except Exception:
                    pass
        except Exception:
            pass
        self._pc = None

        try:
            cap = self._video_capturer
            if cap is not None:
                try:
                    cap.stopCapture()
                except Exception:
                    pass
                try:
                    cap.dispose()
                except Exception:
                    pass
        except Exception:
            pass
        self._video_capturer = None

        for attr in ("_local_video_track", "_local_audio_track", "_video_source", "_audio_source", "_local_stream"):
            try:
                obj = getattr(self, attr, None)
                if obj is not None and hasattr(obj, "dispose"):
                    try:
                        obj.dispose()
                    except Exception:
                        pass
            except Exception:
                pass
            setattr(self, attr, None)

        # Tear down renderers + container.
        try:
            self._clear_views()
        except Exception:
            pass
        try:
            self._remove_container()
        except Exception:
            pass

        self._session_id = 0
        self._role = ""
        self._ice_since_id = 0
        self._connected_fired = False
        self._ws_url = ""
        self._token = ""

        if self._on_disconnected:
            try:
                Clock.schedule_once(lambda *_: self._on_disconnected(), 0)
            except Exception:
                pass

