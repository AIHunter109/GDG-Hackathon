"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CRASHLYTICS_TELEMETRY_SERVICE = exports.CRASHLYTICS_TELEMETRY_RESOURCE_TYPE = exports.CRASHLYTICS_TELEMETRY_SINK_ID = exports.CRASHLYTICS_TELEMETRY_BUCKET_ID = void 0;
exports.onboardCrashlyticsWeb = onboardCrashlyticsWeb;
const ensureApiEnabled_1 = require("../ensureApiEnabled");
const error_1 = require("../error");
const cloudbilling_1 = require("../gcp/cloudbilling");
const cloudlogging_1 = require("../gcp/cloudlogging");
const firebasetelemetry_1 = require("./firebasetelemetry");
const utils_1 = require("../utils");
const apikeys_1 = require("../gcp/apikeys");
const apps_1 = require("../management/apps");
exports.CRASHLYTICS_TELEMETRY_BUCKET_ID = "firebase-telemetry";
exports.CRASHLYTICS_TELEMETRY_SINK_ID = "firebase-telemetry-routing";
exports.CRASHLYTICS_TELEMETRY_RESOURCE_TYPE = "firebasetelemetry.googleapis.com/App";
exports.CRASHLYTICS_TELEMETRY_SERVICE = "firebasetelemetry.googleapis.com";
async function onboardCrashlyticsWeb(projectId, appId, options = {}) {
    const billingEnabled = await (0, cloudbilling_1.checkBillingEnabled)(projectId);
    if (!billingEnabled && options.nonInteractive) {
        throw new error_1.FirebaseError(`Crashlytics requires the Blaze plan, but project ${projectId} is not on the Blaze plan. ` +
            `Please visit https://console.cloud.google.com/billing/linkedaccount?project=${projectId} to upgrade your project.`);
    }
    else if (!billingEnabled) {
        await (0, cloudbilling_1.enableBilling)(projectId, "Crashlytics");
    }
    (0, utils_1.logLabeledBullet)("crashlytics", "Enabling required telemetry APIs...");
    await Promise.all([
        (0, ensureApiEnabled_1.ensure)(projectId, exports.CRASHLYTICS_TELEMETRY_SERVICE, "crashlytics", false),
        (0, ensureApiEnabled_1.ensure)(projectId, "firebasetelemetryadmin.googleapis.com", "crashlytics", false),
    ]);
    (0, utils_1.logLabeledSuccess)("crashlytics", "Telemetry APIs enabled.");
    const appConfig = await (0, apps_1.getAppConfig)(appId, apps_1.AppPlatform.WEB);
    if ("apiKey" in appConfig && appConfig.apiKey) {
        (0, utils_1.logLabeledBullet)("crashlytics", "Ensuring Crashlytics Telemetry API is permitted in API key restrictions...");
        try {
            await (0, apikeys_1.updateAppApiKeyRestriction)({
                apiKey: appConfig.apiKey,
                service: exports.CRASHLYTICS_TELEMETRY_SERVICE,
            });
            (0, utils_1.logLabeledSuccess)("crashlytics", "API key restrictions updated for Crashlytics Telemetry.");
        }
        catch (err) {
            (0, utils_1.logLabeledWarning)("crashlytics", err instanceof Error ? err.message : String(err));
        }
    }
    else {
        (0, utils_1.logLabeledWarning)("crashlytics", `No API key found for this app. If you configure an API key later, ` +
            `please rerun this command or manually add '${exports.CRASHLYTICS_TELEMETRY_SERVICE}' to its allowed APIs in the Google Cloud Console if the key is restricted.`);
    }
    (0, utils_1.logLabeledBullet)("crashlytics", `Setting up Cloud Logging bucket '${exports.CRASHLYTICS_TELEMETRY_BUCKET_ID}'...`);
    const bucket = await (0, cloudlogging_1.createOrUpdateLogBucket)(projectId, exports.CRASHLYTICS_TELEMETRY_BUCKET_ID, "global", true);
    (0, utils_1.logLabeledSuccess)("crashlytics", "Cloud Logging bucket configured.");
    const destination = `logging.googleapis.com/projects/${projectId}/locations/global/buckets/${exports.CRASHLYTICS_TELEMETRY_BUCKET_ID}`;
    const filter = `resource.type="${exports.CRASHLYTICS_TELEMETRY_RESOURCE_TYPE}"`;
    (0, utils_1.logLabeledBullet)("crashlytics", `Setting up Cloud Logging routing sink '${exports.CRASHLYTICS_TELEMETRY_SINK_ID}'...`);
    const sink = await (0, cloudlogging_1.createOrUpdateLogSink)(projectId, exports.CRASHLYTICS_TELEMETRY_SINK_ID, destination, filter);
    (0, utils_1.logLabeledSuccess)("crashlytics", "Cloud Logging routing sink configured.");
    (0, utils_1.logLabeledBullet)("crashlytics", "Configuring Crashlytics telemetry for web app...");
    const config = await (0, firebasetelemetry_1.createOrUpdateTelemetryConfig)(projectId, appId, `projects/${projectId}/locations/global/buckets/${exports.CRASHLYTICS_TELEMETRY_BUCKET_ID}`, 1);
    (0, utils_1.logLabeledSuccess)("crashlytics", "Crashlytics telemetry configured successfully.");
    return { bucket, sink, config };
}
