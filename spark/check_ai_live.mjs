/** Synthetic-only smoke test for Firebase AI Logic on the Spark project. */
import { readFileSync } from "node:fs";
import { initializeApp } from "firebase/app";
import { getAI, getGenerativeModel, GoogleAIBackend } from "firebase/ai";

const config = JSON.parse(readFileSync(new URL("./public/firebase-config.json", import.meta.url), "utf8"));
const app = initializeApp(config);
const ai = getAI(app, { backend: new GoogleAIBackend() });
const model = getGenerativeModel(ai, { model: "gemini-3.6-flash" });
const result = await model.generateContent("Return only the word READY. This is a synthetic connectivity test.");
const answer = result.response.text().trim();
if (!answer.includes("READY")) throw new Error("Unexpected Gemini test response");
console.log("Firebase AI Logic synthetic call succeeded");
