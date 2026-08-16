import { chromium } from "playwright";
const b = await chromium.launch({ channel: "chrome" });
const p = await b.newPage({ viewport: { width: 1440, height: 620 } });
await p.goto("http://127.0.0.1:8000/#/video/3035441c910a", { waitUntil: "networkidle" });
await p.waitForTimeout(2000);
const strip = await p.$(".vod-wrap");
if (strip) { await strip.hover(); await p.waitForTimeout(900); }
await p.screenshot({ path: `${process.env.O}/strip.png` });
await b.close();
