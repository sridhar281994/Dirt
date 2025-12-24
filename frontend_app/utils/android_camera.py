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
        back: int | None = None
        front: int | None = None

        for i in range(n):
            info = CameraInfo()
            Camera.getCameraInfo(i, info)
            ids.append(int(i))
            try:
                facing = int(info.facing)
            except Exception:
                facing = -1

            if facing == int(CameraInfo.CAMERA_FACING_BACK):
                back = int(i)
            elif facing == int(CameraInfo.CAMERA_FACING_FRONT):
                front = int(i)

        if not ids:
            return AndroidCameraIds(back=0, front=1, all_ids=(0, 1))

        if back is None:
            back = ids[0]

        if front is None:
            # If there is a second camera, prefer it. Otherwise fall back to back.
            if len(ids) >= 2:
                front = ids[1] if ids[0] == back else ids[0]
            else:
                front = back

        return AndroidCameraIds(back=int(back), front=int(front), all_ids=tuple(ids))
    except Exception:
        Logger.exception("android_camera: failed to detect camera IDs")
        return AndroidCameraIds(back=0, front=1, all_ids=(0, 1))

