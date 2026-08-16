import { chromium } from "playwright";
const b = await chromium.launch({ channel: "chrome" });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://127.0.0.1:8000/", { waitUntil: "domcontentloaded" });
await p.evaluate(() => localStorage.setItem("theme", "studio"));
await p.goto("http://127.0.0.1:8000/#/video/3035441c910a", { waitUntil: "networkidle" });
await p.waitForTimeout(1400);
await p.screenshot({ path: `${process.env.O}/studio.png` });
await b.close();
