// Writes both accounts' current stories to stories.json (run by the GitHub Actions job).
import { writeFileSync } from "node:fs";
import worker from "../worker/src/index.js";

const res = await worker.fetch(new Request("https://local/"));
const data = await res.json();
for (const e of data.errors) console.error("warn:", e);
for (const a of data.accounts) console.log(`${a.u}: ${a.snaps.length} snaps`);
if (!data.accounts.length) process.exit(1); // keep the last good stories.json
writeFileSync(new URL("../stories.json", import.meta.url), JSON.stringify(data));
