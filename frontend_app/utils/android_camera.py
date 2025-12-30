from __future__ import annotations

from dataclasses import dataclass

from kivy.logger import Logger
from kivy.utils import platform


@dataclass(frozen=True)
class AndroidCameraIds:
    back: int = 0
    front: int = 1
    all_ids: tuple[int, ...] = (0, 1)


def get_android_camera_ids() -> AndroidCameraIds:
    """
    Best-effort mapping of Android camera IDs to front/back.

    Why:
    Some devices expose cameras in different orders; assuming:
      0=back, 1=front
    can make "back cam" render black (wrong camera ID or missing camera).

    Returns sensible defaults for non-Android or failures.
    """
    if platform != "android":
        return AndroidCameraIds(back=0, front=1, all_ids=(0, 1))

    try:
        from jnius import autoclass  # type: ignore

        Camera = autoclass("android.hardware.Camera")
        CameraInfo = autoclass("android.hardware.Camera$CameraInfo")

        n = int(Camera.getNumberOfCameras())
        ids: list[int] = []
        back_ids: list[int] = []
        front_ids: list[int] = []

        for i in range(n):
            info = CameraInfo()
            Camera.getCameraInfo(i, info)
            ids.append(int(i))
            try:
                facing = int(info.facing)
            except Exception:
                facing = -1

            if facing == int(CameraInfo.CAMERA_FACING_BACK):
                back_ids.append(int(i))
            elif facing == int(CameraInfo.CAMERA_FACING_FRONT):
                front_ids.append(int(i))

        if not ids:
            return AndroidCameraIds(back=0, front=1, all_ids=(0, 1))

        # IMPORTANT (OEM quirk):
        # Many devices report multiple BACK cameras (wide/tele/macro). Access to
        # auxiliary cameras is often restricted unless your package is whitelisted
        # (logcat: "Access denied finding property vendor.camera.aux.packagelist").
        #
        # If we accidentally pick an auxiliary BACK camera here, Kivy's legacy
        # camera backend may open it but render black. To minimize that:
        # - Prefer BACK id 0 if it is back-facing
        # - Else pick the *lowest* back-facing id
        # - Similar logic for FRONT (prefer 1 if it is front-facing)
        if back_ids:
            back = 0 if 0 in back_ids else min(back_ids)
        else:
            back = ids[0]

        if front_ids:
            front = 1 if 1 in front_ids else min(front_ids)
        else:
            # If there is a second camera, prefer it. Otherwise fall back to back.
            if len(ids) >= 2:
                front = ids[1] if ids[0] == back else ids[0]
            else:
                front = back

        out = AndroidCameraIds(back=int(back), front=int(front), all_ids=tuple(ids))
        Logger.info(
            "android_camera: cameras=%s back_ids=%s front_ids=%s selected back=%s front=%s",
            list(ids),
            list(back_ids),
            list(front_ids),
            out.back,
            out.front,
        )
        return out
    except Exception:
        Logger.exception("android_camera: failed to detect camera IDs")
        return AndroidCameraIds(back=0, front=1, all_ids=(0, 1))

