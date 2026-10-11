// Railway Infrastructure as Code for the app services (replaces the
// deprecated railway*.toml config files). Railway does NOT read this file
// on deploy — changes reach Railway only via `railway config plan` then
// `railway config apply`, run from this directory or the repo root.
// Handbook: operations/railway.md.
//
// A named partial: this file owns only what it declares. Postgres, Redis and
// their volumes are deliberately left out, so an apply can never touch them.
// Removing a service or `webVolume` below DELETES it on the next apply.
import { defineRailway, github, project, service, volume } from "railway/iac";

export const partial = "dogboxx-app";

export default defineRailway(() => {
  const repo = github("MarcusGrazette/dogboxx-booking-app", { branch: "main", checkSuites: false });
  const region = "europe-west4-drams3a";
  // Every service built with nixpacks under Config as Code; scripts rely on
  // its /opt/venv layout (e.g. `railway ssh … /opt/venv/bin/flask`).
  const build = { builder: "NIXPACKS" as const };

  // Photo uploads, mounted on web only. Destructive if removed or shrunk.
  const webVolume = volume("web-volume", {
    alerts: { usage: { "100": {}, "80": {}, "95": {} } },
    allowOnlineResize: true,
    region,
    sizeMB: 5000,
  });

  const web = service("web", {
    source: repo,
    build,
    // Volume symlink → migrations → gunicorn (gevent). Not the old dashboard
    // command: that one has no gevent worker and no uploads symlink.
    start: "bash scripts/start.sh",
    healthcheck: "/health",
    healthcheckTimeout: 60,
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 3 },
    replicas: { [region]: 1 },
    domains: ["app.dogboxx.org"],
    volumeMounts: { "/data/uploads": webVolume },
  });

  // One-shot Flask CLI jobs: no healthcheck (they never open a port), never
  // restarted. Schedules are UTC.
  const cron = (name: string, start: string, cronSchedule: string, extra = {}) =>
    service(name, {
      source: repo,
      build,
      start,
      replicas: { [region]: 1 },
      deploy: { cronSchedule, restartPolicyType: "NEVER" },
      ...extra,
    });

  const sessionCleanup = cron(
    "session-cleanup",
    "flask session_cleanup && flask sweep-push-subscriptions",
    "0 4 * * *",
  );
  const reconcileUploads = cron(
    "reconcile-uploads",
    "flask reconcile-uploads",
    "0 3 * * 1",
    { networking: { privateNetworkEndpoint: "reconcile-web-data-uploads" } },
  );
  // 07/08 UTC cover 08:00 London in BST/GMT, 14/15 cover 15:00; the command
  // checks the London clock and only the matching run sends.
  const walkerSummaries = cron(
    "walker-summaries",
    "flask send-walker-summaries",
    "0 7,8,14,15 * * 1-5",
  );

  return project("disciplined-creation", {
    variables: { managed: false },
    resources: [web, webVolume, sessionCleanup, reconcileUploads, walkerSummaries],
  });
});
