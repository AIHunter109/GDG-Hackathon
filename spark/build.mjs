import { readFile, writeFile } from "node:fs/promises";

const source = await readFile(new URL("../main/dashboard.html", import.meta.url), "utf8");
const performance = JSON.parse(await readFile(new URL("../main/verified_performance.json", import.meta.url), "utf8"));
const marker = /(<script>\r?\n\s*const FIELDS = \[)/;
if (!marker.test(source) || !source.includes("</body>")) {
  throw new Error("Dashboard HTML entry points changed; update the Spark build adapter");
}
const output = source
  .replace(marker, `<script>window.SPARK_MODE = true; window.SEAL_VERIFIED_PERFORMANCE = ${JSON.stringify(performance)};</script>\n        $1`)
  .replace("    </body>", "        <script type=\"module\" src=\"/src/app.js\"></script>\n    </body>");
await writeFile(new URL("index.html", import.meta.url), output);
