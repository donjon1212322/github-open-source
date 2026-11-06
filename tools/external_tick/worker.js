// Cloudflare Worker: an external clock for the GHOS watchdog.
//
// GitHub's `schedule` event is best-effort: on 06.11.2026 it dropped whole
// batches of runs. This Worker calls the GitHub REST API instead, which
// creates a `repository_dispatch` run immediately. The workflow stays on
// GitHub; only the clock moves outside.
//
// Secrets (wrangler secret put GH_PAT) and vars (GH_REPO) are set at deploy
// time. NEVER commit a token to this repository.

export default {
  async scheduled(event, env, ctx) {
    const repo = env.GH_REPO;
    const token = env.GH_PAT;
    if (!repo || !token) {
      console.error("GH_REPO / GH_PAT are not configured");
      return;
    }
    const response = await fetch(
      `https://api.github.com/repos/${repo}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "Content-Type": "application/json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "ghos-v6-external-tick",
        },
        body: JSON.stringify({ event_type: "watchdog_tick" }),
      },
    );
    if (!response.ok) {
      console.error("tick failed", response.status, await response.text());
    } else {
      console.log("tick ok", response.status, event.scheduledTime);
    }
  },

  // Health-check endpoint; the cron trigger is what actually runs the tick.
  async fetch() {
    return new Response("ghos-v6 external tick: cron trigger active", {
      status: 200,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  },
};
