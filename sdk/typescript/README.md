# chat-engine-client (TypeScript)

بلا تبعيات. المتصفح و Node ≥ 18.

```ts
import { ChatEngine } from "chat-engine-client";
const ce = new ChatEngine({ baseUrl: "http://localhost:8080" });
const r = await ce.chat({ message: "مرحبا" });
for await (const ev of ce.chatStream({ message: "تابع", conversation_id: r.conversation_id })) {
  if (ev.type === "message_delta") process.stdout.write(String(ev.data.delta));
}
```
