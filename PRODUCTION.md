## Production checklist (Buddymeet Android)

### Build output + Play Store readiness
- **AAB builds**: `buildozer.spec` sets `android.release_artifact = aab`
- **Versioning**:
  - `version = ...` is your Play Store **versionName**
  - `android.numeric_version = ...` is your **versionCode** (must increase every upload)
- **Privacy defaults**:
  - `android.private_storage = True`
  - `android.allow_backup = False`

### Release signing (required)
Create an **upload keystore** and configure Buildozer locally (don’t commit secrets):
- `android.release_keystore`
- `android.release_keyalias`
- `android.release_keystore_passwd`
- `android.release_keyalias_passwd`

### Secure auth token storage (required)
The app now stores the login token using **Android Keystore-backed encrypted prefs**
via AndroidX Security Crypto. Plaintext token storage in `JsonStore` is avoided and
existing installs migrate on first run.

### Permissions (required for video)
Declared in `buildozer.spec` and requested at runtime from `frontend_app/main.py`:
- Camera
- Microphone

### Backend configuration
`frontend_app/utils/api.py` uses:
- `BACKEND_URL` env var when present
- otherwise defaults to your Render URL

For production builds, set the production backend URL and keep it stable.

### WebRTC at scale (recommended)
The backend supports **WebSocket signaling** (preferred) plus a **Redis-backed matchmaking queue**.

- **Redis (required for scaling)**
  - Set `REDIS_URL` for the backend (e.g. `redis://...`).
  - Without Redis, the backend falls back to legacy DB-based random matching and HTTP-poll signaling (not scalable).

- **WebSocket signaling**
  - Endpoint: `/api/ws` (client connects with `?token=<JWT>`).
  - This removes WebRTC offer/answer/ICE from Postgres and uses Redis pub/sub fanout.

- **TURN (coturn)**
  - Optionally set:
    - `TURN_URLS` (comma-separated `turn:` / `turns:` URLs)
    - `TURN_SECRET` (coturn `--static-auth-secret` / `--use-auth-secret`)
    - `TURN_TTL_SECONDS` (default 600)
  - The backend will include TURN REST credentials in `webrtc.ice_servers`.

