/** Nomisma: telemetry and automatic exception uploads are disabled. */
import posthog from 'posthog-js';
// Keep the existing provider interface without initializing a network client.
export function initPostHog():void {}
export async function syncAnalyticsSettings():Promise<void> {}
export {posthog};
