# Mobile App + Cloud Relay

## Goal

Give users a native mobile app that can talk to their home/office desktop Auctus Agent without requiring a public IP, Telegram, Feishu, Lark, or manual WebSocket setup.

The user experience should be:

1. Download the mobile app.
2. Register or log in to Auctus API.
3. Download the desktop Agent.
4. Bind the desktop Agent to the account by scanning a QR code.
5. Send a task from the mobile app.
6. The cloud Relay forwards the task to the matching desktop Agent.
7. The desktop Agent executes locally and sends the result back to the app.

## Product Principle

Users should not configure networking.

Both clients initiate outbound connections:

- Mobile app -> Auctus API over HTTPS/WebSocket.
- Desktop Agent -> Auctus Relay over WebSocket.

Because the desktop Agent dials out to the cloud Relay, the user does not need a public IP, port forwarding, or a custom domain.

## Architecture

```text
Mobile App
  -> Auctus API
      -> Auth / Billing / Device Registry
      -> Relay Hub
          <-> Desktop Agent WebSocket
      <- Task Result
```

Desktop Agent maintains a long-lived tunnel:

```text
wss://api.auctus.ai/relay/tunnel/{device_id}
```

The mobile app sends a task:

```http
POST /api/mobile/tasks
Authorization: Bearer <user_jwt>

{
  "device_id": "desktop_abc",
  "message": "Summarize the contract on my Desktop"
}
```

Relay forwards the task to the desktop:

```json
{
  "type": "agent_task",
  "task_id": "task_123",
  "user_id": "user_456",
  "device_id": "desktop_abc",
  "message": "Summarize the contract on my Desktop"
}
```

Desktop returns the result:

```json
{
  "type": "agent_result",
  "task_id": "task_123",
  "reply": "Summary...",
  "files": []
}
```

## Core Components

### Auth

- Email/password or OAuth login.
- JWT/session tokens for the mobile app.
- Device tokens for desktop Agent tunnels.
- Token rotation and revoke support.

### Device Binding

Desktop Agent shows a QR code containing a short-lived binding code:

```json
{
  "bind_code": "ABCD-1234",
  "device_id": "desktop_abc",
  "expires_at": "..."
}
```

Mobile app scans the QR code and confirms:

```http
POST /api/devices/bind
Authorization: Bearer <user_jwt>

{
  "bind_code": "ABCD-1234"
}
```

Server binds:

```text
user_id -> device_id -> device_token
```

The desktop Agent then uses its device token to connect to Relay.

### Relay Hub

The Relay keeps an in-memory and persistent mapping:

```text
user_id -> device_id -> active websocket connection
```

It must support:

- Desktop online/offline status.
- Heartbeats and reconnect.
- Task dispatch.
- Result routing.
- Task timeout.
- Multiple devices per user.

### Task Queue

Tasks need durable state:

- `queued`
- `sent_to_device`
- `running`
- `needs_permission`
- `completed`
- `failed`
- `timed_out`

The queue should survive Relay restart. Redis/Postgres is preferred for production.

### Permission Model

Mobile tasks can trigger local desktop actions. The cloud must not blindly grant local permissions.

Desktop Agent remains the authority for:

- File access scope.
- Terminal access.
- Calendar/reminder access.
- High-risk actions.

If a task needs permission, desktop Agent returns:

```json
{
  "type": "permission_request",
  "task_id": "task_123",
  "permission_type": "files",
  "message": "Allow access to ~/Desktop?"
}
```

Mobile app can show the request, but desktop policy decides whether mobile approval is enough. For sensitive operations, require desktop-side approval or a user-configured "trusted mobile" setting.

### Files

Generated files can be handled in two ways:

1. Desktop-local only: mobile receives file metadata and asks desktop to upload/download on demand.
2. Cloud temporary upload: desktop uploads selected output files to Auctus API and mobile receives expiring URLs.

For product UX, use temporary cloud upload for outputs and previews, with expiry and size limits.

### Push Notifications

Mobile app should receive push notifications for:

- Task completed.
- Permission required.
- Desktop offline.
- Desktop reconnected.

## API Sketch

```text
POST   /api/auth/register
POST   /api/auth/login
GET    /api/devices
POST   /api/devices/bind/start          # desktop creates bind code
POST   /api/devices/bind/confirm        # mobile scans QR and confirms
POST   /api/devices/{device_id}/revoke
GET    /api/devices/{device_id}/status

WS     /relay/tunnel/{device_id}        # desktop Agent long connection
POST   /api/mobile/tasks
GET    /api/mobile/tasks/{task_id}
WS     /api/mobile/tasks/stream         # optional live result stream
POST   /api/mobile/tasks/{task_id}/permission
GET    /api/mobile/files/{file_id}
```

## Security Requirements

- Bind codes must be short-lived and single-use.
- Device tokens must be scoped to one user and one device.
- A user can only dispatch tasks to their own devices.
- Relay messages must include `task_id`, `user_id`, and `device_id`.
- Desktop Agent must reject tasks whose signed user/device claims do not match local binding.
- Do not store full conversation content in Relay by default; store metadata and task state. If cloud history is enabled, make it explicit.
- Generated file URLs must expire.
- High-risk local operations need explicit permission.

## Delivery Plan

1. Add production device registry to Auctus API.
2. Add QR bind-code flow in desktop Agent.
3. Add desktop Relay WebSocket client with reconnect and heartbeat.
4. Add mobile task API and task state table.
5. Add Relay dispatch from mobile task -> desktop tunnel.
6. Add desktop result -> Relay -> mobile result.
7. Add mobile app login, device list, task composer, task result view.
8. Add file output upload/download.
9. Add permission request flow.
10. Add push notifications.
11. Add observability: task logs, tunnel status, timeout metrics.
12. Harden security and rate limits.

## Open Decisions

- Native app stack: Expo/React Native vs Swift/Kotlin.
- Cloud region strategy: global Vercel, CN Aliyun, or both.
- Whether mobile approval can authorize file/terminal/calendar actions.
- Whether task content is stored in cloud history or only transient Relay state.
- File upload limits and retention period.
