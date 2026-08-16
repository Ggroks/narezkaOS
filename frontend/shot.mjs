import { chromium } from "playwright";
const b = await chromium.launch({ channel: "chrome" });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
for (const [name, url, theme] of [
  ["catalog","http://127.0.0.1:8000/","hardware"],
  ["stage","http://127.0.0.1:8000/#/video/3035441c910a","hardware"],
  ["light","http://127.0.0.1:8000/","workshop"],
]) {
  await p.goto("http://127.0.0.1:8000/", { waitUntil: "domcontentloaded" });
  await p.evaluate((t) => localStorage.setItem("theme", t), theme);
  await p.goto(url, { waitUntil: "networkidle" });
  await p.waitForTimeout(1400);
  await p.screenshot({ path: `${process.env.O}/${name}.png` });
}
await b.close();
