// Cloudflare Worker: an external, minute-accurate clock for GHOS.
//
// GitHub's `schedule` event is best-effort: on 06-07.11.2026 it delayed runs
// by hours and dropped whole batches. This Worker calls the GitHub REST API
// instead: a `workflow_dispatch` run is created immediately, bypassing the
// shared scheduled-event queue. GitHub cron stays as a fallback, and the
// watchdog stays as the second line of defence.
//
// Free plan economics (official Cloudflare limits, 2026):
//   ONE cron trigger "* * * * *" = 1440 invocations/day, ~1.44% of the
//   100,000 requests/day allowance; 10 ms CPU each; 1 of 5 allowed triggers.
//   Only DUE minutes call GitHub (~23 workflow_dispatch + 48 watchdog ticks
//   per day), so the extra load on GitHub Actions is small.
//   Cron Triggers have NO retries and NO failure alerts - hence the fallback.
//
// Secrets (wrangler secret put GH_PAT) and vars (GH_REPO) are set at deploy
// time. NEVER commit a token to this repository.

// UTC "HH:MM" -> workflow_dispatch inputs. Keep in sync with pipeline.yml.
// Third element is an optional UTC day-of-week (0 = Sunday).
const SLOTS = [
  // Owner 12.11: US audience. Fixed UTC minutes keep evening ET and
  // morning PT inside their windows in both winter and summer time.
  ["00:23", { command: "collect" }],
  ["01:13", { command: "publish", slot: "us_evening" }],   // 20:13 EST / 17:13 PST
  ["13:33", { command: "article" }],
  ["16:23", { command: "publish", slot: "us_morning" }],   // 11:23 EST / 08:23 PST
  ["18:13", { command: "publish", slot: "us_midday" }],    // 13:13 EST / 10:13 PST
  ["19:18", { command: "news" }],
  ["19:53", { command: "news-article" }],
  ["20:23", { command: "metrics" }],
  ["00:17", { command: "crossposts" }],
  ["18:17", { command: "crossposts" }],
  ["19:17", { command: "crossposts" }],
  ["20:17", { command: "crossposts" }],
  ["21:17", { command: "crossposts" }],
  ["22:17", { command: "crossposts" }],
  ["23:17", { command: "crossposts" }],
  ["00:47", { command: "moderation" }],
  ["03:47", { command: "moderation" }],
  ["06:47", { command: "moderation" }],
  ["09:47", { command: "moderation" }],
  ["12:47", { command: "moderation" }],
  ["15:47", { command: "moderation" }],
  ["18:47", { command: "moderation" }],
  ["21:47", { command: "moderation" }],
  // Weekly cycle (owner 08.11): these commands now write an ISO-week marker
  // in state, so a second trigger is a no-op instead of a duplicate post.
  // With the guard in place they can be dispatched on the exact minute.
  // JS getUTCDay(): 0 = Sunday, 4 = Thursday.
  ["16:13", { command: "digest" }, 0],
  ["16:43", { command: "report" }, 0],
  ["18:53", { command: "poll-close" }, 4],
  // Facebook is deliberately NOT added here, even after the 12.11 redeploy
  // for the US-audience slots: the owner asked to keep Make/Sheet untouched.
  // facebook_handoff_command() now carries a per-UTC-day idempotency marker,
  // and watchdog.SLOTS dispatches the handoff on the 18:27/18:30 ticks.
  // Make/Sheet/webhook stay untouched.
];

function minuteKey(date) {
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function ghHeaders(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "Content-Type": "application/json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "ghos-v6-external-tick",
  };
}

async function dispatchWorkflow(repo, token, inputs, ref) {
  const url = `https://api.github.com/repos/${repo}/actions/workflows/pipeline.yml/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: ghHeaders(token),
    body: JSON.stringify({ ref, inputs }),
  });
  if (!response.ok) {
    console.error("workflow_dispatch failed", inputs.command, response.status, await response.text());
    return false;
  }
  console.log("workflow_dispatch ok", inputs.command, response.status);
  return true;
}

async function dispatchWatchdogTick(repo, token) {
  const response = await fetch(`https://api.github.com/repos/${repo}/dispatches`, {
    method: "POST",
    headers: ghHeaders(token),
    body: JSON.stringify({ event_type: "watchdog_tick" }),
  });
  if (!response.ok) {
    console.error("tick failed", response.status, await response.text());
  } else {
    console.log("tick ok", response.status);
  }
}

export default {
  async scheduled(event, env) {
    const repo = env.GH_REPO;
    const token = env.GH_PAT;
    if (!repo || !token) {
      console.error("GH_REPO / GH_PAT are not configured");
      return;
    }
    // event.scheduledTime is the INTENDED minute, not the (jittered) arrival.
    const moment = new Date(event.scheduledTime || Date.now());
    const key = minuteKey(moment);
    const day = moment.getUTCDay();
    const ref = env.GH_REF || "main";

    for (const [at, inputs, weekday] of SLOTS) {
      if (at !== key || (weekday !== undefined && weekday !== day)) continue;
      await dispatchWorkflow(repo, token, inputs, ref);
    }
    // Watchdog still runs every 30 minutes as the safety net.
    const minute = moment.getUTCMinutes();
    if (minute === 0 || minute === 30) {
      await dispatchWatchdogTick(repo, token);
    }
  },

  // Health-check endpoint; the cron trigger is what actually runs the tick.
  async fetch() {
    return new Response("ghos-v6 external tick: minute cron active", {
      status: 200,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  },
};
