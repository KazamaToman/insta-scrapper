/* instascrapper UI — vanilla JS, no build step. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const S = {
    view: "scrape",
    username: null,
    profile: null,
    posts: [],
    selected: new Set(),
    filter: "all",
    demo: false,
    jobId: null,
    events: null,
    poll: null,
    pollTimer: null,
    jobFiles: new Map(),
    modalIndex: -1,
    libraryLoaded: false,
    libraryDirty: false,
    activeAccount: null,
  };

  /* ------------------------------------------------------------ tiny helpers */
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  const fmtInt = (n) => {
    const value = Number(n) || 0;
    if (value >= 1_000_000) return (value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1) + "M";
    if (value >= 1_000) return (value / 1_000).toFixed(value >= 10_000 ? 0 : 1) + "K";
    return String(value);
  };

  const fmtBytes = (bytes) => {
    const value = Number(bytes) || 0;
    if (value < 1024) return value + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let size = value / 1024;
    let i = 0;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
    return size.toFixed(1) + " " + units[i];
  };

  const fmtDate = (unix) => {
    if (!unix) return "—";
    return new Date(unix * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  };

  let toastTimer = null;
  const toast = (message) => {
    const el = $("#toast");
    el.textContent = message;
    el.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("is-visible"), 2600);
  };

  async function api(path, options = {}) {
    const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
    let data = null;
    try { data = await response.json(); } catch (_) { /* empty body */ }
    if (!response.ok) {
      const error = new Error((data && data.error) || `Request failed (${response.status})`);
      error.kind = (data && data.kind) || "http";
      error.status = response.status;
      throw error;
    }
    return data;
  }

  /* ----------------------------------------------------------------- views */
  function setView(view) {
    S.view = view;
    $$(".tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === view));
    $("#view-scrape").classList.toggle("is-active", view === "scrape");
    $("#view-library").classList.toggle("is-active", view === "library");
    if (view === "library" && (!S.libraryLoaded || S.libraryDirty)) loadLibrary();
    if (view === "scrape") $("#query").focus({ preventScroll: true });
  }

  /* ---------------------------------------------------------------- banners */
  function showBanner(kind, html) {
    const banner = $("#banner");
    banner.className = `banner is-${kind}`;
    banner.innerHTML = `<div class="banner-icon">${kind === "error" ? "⚠" : kind === "warn" ? "!" : "i"}</div><div>${html}</div>`;
  }
  function hideBanner() {
    $("#banner").className = "banner hidden";
    $("#banner").innerHTML = "";
  }

  /* -------------------------------------------------------------- skeletons */
  const skeletonMarkup = () =>
    Array.from({ length: 12 }, () => `<div class="tile sk"></div>`).join("");

  function showSkeletons(on) {
    $("#skeletons").innerHTML = on ? skeletonMarkup() : "";
    $("#skeletons").classList.toggle("hidden", !on);
    if (on) {
      $("#hero").classList.add("hidden");
      $("#grid").innerHTML = "";
      $("#toolbar").classList.add("hidden");
    }
  }

  /* ----------------------------------------------------------- profile load */
  async function loadProfile(username) {
    const name = username.trim();
    if (!name) return;
    const limit = Number($("#limit").value) || 24;
    hideBanner();
    showSkeletons(true);
    setView("scrape");

    try {
      const data = await api(`/api/profile/${encodeURIComponent(name)}?limit=${limit}`);
      applyProfile(data.profile, data.posts, {
        demo: !!data.demo,
        authenticated: !!data.authenticated,
      });
      sessionStatus(data.authenticated);
      history.replaceState(null, "", `#/@${data.profile.username}`);
    } catch (error) {
      showSkeletons(false);
      renderFailure(error, name);
    }
  }

  function clearProfile() {
    S.profile = null;
    S.posts = [];
    S.selected.clear();
    $("#grid").innerHTML = "";
    $("#toolbar").classList.add("hidden");
    $("#p-name").textContent = "No account loaded";
    $("#p-handle").textContent = "Search a public profile to begin";
    $("#p-stats").hidden = true;
    $("#p-bio").hidden = true;
    $("#p-actions").hidden = true;
    $("#avatar").className = "avatar placeholder-avatar";
    $("#avatar").style.backgroundImage = "";
    $("#selection-bar").classList.remove("is-visible");
  }

  function renderFailure(error, name) {
    clearProfile();
    const messages = {
      not_found: `<strong>@${esc(name)} not found</strong>Instagram returned 404. Check the spelling — usernames are case-insensitive but typos 404.`,
      private: `<strong>Private account</strong>@${esc(name)} is private. Only public accounts can be scraped.`,
      login_required: `<strong>Instagram wants a session</strong>Anonymous requests were refused. Export your browser cookies to <code>cookies.txt</code> and restart with <code>instascrapper serve --cookies cookies.txt</code>.`,
      rate_limited: `<strong>Rate limited</strong>Instagram is throttling this IP. Wait a few minutes, or restart with a larger <code>--delay</code>.`,
      network: `<strong>Instagram is unreachable from this server</strong>${esc(error.message)}<div class="banner-actions"><button class="btn btn-secondary btn-sm" data-action="demo">Load demo dataset instead</button></div>`,
      http: `<strong>Request failed</strong>${esc(error.message)}`,
    };
    showBanner("error", messages[error.kind] || messages.http);
    $("#hero").classList.remove("hidden");
    $("#empty").classList.add("hidden");
  }

  function applyProfile(profile, posts, meta = {}) {
    S.profile = profile;
    S.username = profile.username;
    S.posts = posts || [];
    S.demo = !!meta.demo;
    S.selected.clear();
    S.filter = "all";
    showSkeletons(false);
    $("#hero").classList.add("hidden");
    renderProfile();
    renderGrid();
    $("#download-all").disabled = S.posts.length === 0;
    $("#toolbar").classList.toggle("hidden", S.posts.length === 0);

    if (S.demo) {
      showBanner(
        "info",
        `<strong>Demo dataset — Instagram could not be reached</strong>
         These 8 sample posts live inside this app, so the download pipeline is fully exercised.
         Run <code>instascrapper serve</code> on a machine with normal internet access to scrape real accounts.`
      );
    }
  }

  function renderProfile() {
    const p = S.profile;
    if (!p) return;
    $("#avatar").className = "avatar";
    if (p.profile_pic_url) {
      $("#avatar").style.backgroundImage = `url("${p.profile_pic_url}")`;
    } else {
      $("#avatar").style.backgroundImage = "var(--grad)";
    }
    $("#p-name").innerHTML = `${esc(p.full_name || p.username)}${p.is_verified ? ' <span title="verified">✓</span>' : ""}`;
    $("#p-handle").textContent = "@" + p.username;
    $("#p-stats").hidden = false;
    $("#stat-posts").textContent = fmtInt(p.posts_count);
    $("#stat-followers").textContent = fmtInt(p.followers);
    $("#stat-following").textContent = fmtInt(p.following);
    $("#p-bio").hidden = !p.biography;
    $("#p-bio").textContent = p.biography || "";
    $("#p-actions").hidden = false;
    $("#open-instagram").href = p.url || `https://www.instagram.com/${p.username}/`;
    $("#download-all").disabled = S.posts.length === 0;
  }

  /* ------------------------------------------------------------------ grid */
  const visiblePosts = () =>
    S.posts.filter((post) => {
      if (S.filter === "all") return true;
      if (S.filter === "video") return post.media_type === "video" || (post.media || []).some((m) => m.media_type === "video");
      if (S.filter === "image") return post.media_type === "image";
      if (S.filter === "carousel") return post.media_type === "carousel";
      return true;
    });

  function renderGrid() {
    const grid = $("#grid");
    const posts = visiblePosts();
    if (!posts.length) {
      grid.innerHTML = "";
      const empty = $("#empty");
      empty.classList.remove("hidden");
      empty.innerHTML = S.posts.length
        ? "<h3>No posts match this filter</h3>Try “All”."
        : "<h3>No posts returned</h3>This account may have no public posts, or Instagram withheld them.";
      updateSelectionUI();
      return;
    }
    $("#empty").classList.add("hidden");

    grid.innerHTML = posts
      .map((post, index) => {
        const thumb = post.thumbnail_url || (post.media && post.media[0] && post.media[0].url) || "";
        const badges = [];
        if (post.media_type === "carousel") badges.push(`<span class="badge">${post.media_count} ⧉</span>`);
        if (post.media_type === "video" || (post.media || []).some((m) => m.media_type === "video")) badges.push('<span class="badge video">▶ video</span>');
        return `
          <article class="tile${S.selected.has(post.shortcode) ? " is-selected" : ""}" data-shortcode="${esc(post.shortcode)}" data-index="${index}" style="animation-delay:${Math.min(index * 18, 320)}ms">
            ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}
            <button class="tick" type="button" data-role="tick" aria-label="Select post"></button>
            <div class="badges">${badges.join("")}</div>
            <div class="meta"><span>${fmtDate(post.taken_at)}</span><span class="likes">♥ ${fmtInt(post.like_count)}</span></div>
          </article>`;
      })
      .join("");

    grid.querySelectorAll(".tile").forEach((tile) => {
      tile.addEventListener("click", (event) => {
        const shortcode = tile.dataset.shortcode;
        if (event.target.closest('[data-role="tick"]')) {
          toggleSelect(shortcode);
        } else {
          openModal(Number(tile.dataset.index));
        }
      });
    });
    updateSelectionUI();
  }

  function toggleSelect(shortcode, force) {
    const shouldSelect = force === undefined ? !S.selected.has(shortcode) : force;
    if (shouldSelect) S.selected.add(shortcode);
    else S.selected.delete(shortcode);
    const tile = document.querySelector(`.tile[data-shortcode="${CSS.escape(shortcode)}"]`);
    if (tile) tile.classList.toggle("is-selected", shouldSelect);
    updateSelectionUI();
  }

  function updateSelectionUI() {
    const count = S.selected.size;
    const visible = visiblePosts();
    const allVisibleSelected = visible.length > 0 && visible.every((post) => S.selected.has(post.shortcode));
    const someVisibleSelected = visible.some((post) => S.selected.has(post.shortcode));

    const selectAll = $("#select-all");
    selectAll.checked = allVisibleSelected;
    selectAll.indeterminate = !allVisibleSelected && someVisibleSelected;
    $("#selected-label").textContent = count ? `${count} selected` : `${visible.length} posts`;

    $("#selection-bar").classList.toggle("is-visible", count > 0);
    $("#sel-count").textContent = String(count);
    $("#toolbar-download").disabled = count === 0;
    $("#toolbar-download").textContent = count ? `Download ${count}` : "Download selected";
  }

  /* -------------------------------------------------------------- selection */
  function selectAllVisible() {
    const visible = visiblePosts();
    const all = visible.every((post) => S.selected.has(post.shortcode));
    visible.forEach((post) => (all ? S.selected.delete(post.shortcode) : S.selected.add(post.shortcode)));
    renderGrid();
  }

  /* --------------------------------------------------------------- download */
  async function startDownload(shortcodes, label) {
    if (!S.profile) return;
    const list = (shortcodes || []).filter(Boolean);
    if (!list.length) {
      toast("Nothing selected");
      return;
    }
    try {
      const response = await api("/api/download", {
        method: "POST",
        body: JSON.stringify({
          username: S.profile.username,
          shortcodes: list,
          include_videos: true,
          overwrite: false,
          demo: S.demo,
        }),
      });
      watchJob(response.job_id, label || `@${S.profile.username}`);
    } catch (error) {
      toast(error.message);
    }
  }

  /* ------------------------------------------------------------- job panel */
  function resetJobPanel(title) {
    S.jobFiles.clear();
    $("#job-title").textContent = title;
    $("#job-sub").textContent = "preparing…";
    $("#job-files").innerHTML = "";
    $("#job-stats").textContent = "";
    $("#job-progress").style.width = "6%";
    $("#job-progress").style.background = "";
    $("#job-progress").classList.add("is-indeterminate");
    $("#job-cancel").classList.remove("hidden");
    $("#job-close").classList.add("hidden");
    $("#job-view").classList.add("hidden");
    $("#job-panel").classList.add("is-open");
  }

  function watchJob(jobId, title) {
    stopWatching();
    S.jobId = jobId;
    resetJobPanel(`Downloading ${title}`);
    S.jobTotalPosts = 0;
    S.jobSeenPosts = 0;

    if (typeof EventSource === "undefined") {
      pollJob(jobId);
      return;
    }
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    S.events = source;

    source.onmessage = (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch (_) { return; }
      handleJobEvent(event);
    };
    source.addEventListener("end", (message) => {
      try { handleJobEvent(JSON.parse(message.data), true); } catch (_) { /* ignore */ }
      stopWatching();
    });
    source.onerror = () => {
      // Stream dropped: fall back to polling until the job reports a final state.
      if (!S.events) return; // already finished cleanly
      stopWatching();
      pollJob(jobId);
    };
  }

  function stopWatching() {
    if (S.events) { S.events.close(); S.events = null; }
    if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
  }

  function pollJob(jobId) {
    const tick = async () => {
      try {
        const snapshot = await api(`/api/jobs/${jobId}`);
        if (snapshot.status !== "running" && snapshot.status !== "queued") {
          clearInterval(S.pollTimer);
          S.pollTimer = null;
          finaliseJob(snapshot.status, snapshot.summary, snapshot.error);
        }
      } catch (_) { /* keep polling */ }
    };
    S.pollTimer = setInterval(tick, 900);
    tick();
  }

  function handleJobEvent(event, isFinal) {
    switch (event.type) {
      case "account": {
        S.jobTotalPosts = event.posts || 0;
        $("#job-sub").textContent = `${event.posts} post(s) queued`;
        break;
      }
      case "post": {
        S.jobSeenPosts += 1;
        if (S.jobTotalPosts) {
          $("#job-sub").textContent = `post ${Math.min(S.jobSeenPosts, S.jobTotalPosts)} of ${S.jobTotalPosts}`;
          const pct = Math.max(6, Math.round((S.jobSeenPosts / S.jobTotalPosts) * 100));
          $("#job-progress").style.width = pct + "%";
        }
        break;
      }
      case "file": {
        addJobFile(event.filename, event.status, event.bytes, event.error);
        break;
      }
      case "warning": {
        $("#job-sub").textContent = event.message;
        break;
      }
      case "job": {
        if (isFinal || ["done", "error", "cancelled"].includes(event.status)) {
          finaliseJob(event.status, null, event.error);
        }
        break;
      }
    }
    if (isFinal) {
      const summary = event.summary;
      finaliseJob(event.status || (event.error ? "error" : "done"), summary, event.error);
    }
  }

  function addJobFile(filename, status, bytes, error) {
    const list = $("#job-files");
    let row = S.jobFiles.get(filename);
    if (!row) {
      row = document.createElement("li");
      row.innerHTML = `<span class="state"></span><span class="fname"></span><span class="size"></span>`;
      list.appendChild(row);
      S.jobFiles.set(filename, row);
      while (list.children.length > 60) {
        const first = list.firstElementChild;
        S.jobFiles.delete(first.querySelector(".fname").textContent);
        first.remove();
      }
    }
    row.querySelector(".fname").textContent = filename;
    const state = row.querySelector(".state");
    const size = row.querySelector(".size");
    const map = {
      downloaded: ["ok", "✓"],
      skipped: ["skip", "="],
      failed: ["err", "✗"],
      planned: ["skip", "·"],
    };
    const [cls, glyph] = map[status] || ["skip", "•"];
    state.className = "state " + cls;
    state.textContent = glyph;
    size.textContent = bytes ? fmtBytes(bytes) : (error ? "failed" : "");
    if (status === "failed") row.title = error || "failed";
    if (status === "downloaded") row.dataset.bytes = String(bytes || 0);
    list.scrollTop = list.scrollHeight;

    const files = [...S.jobFiles.values()];
    const done = files.filter((r) => r.querySelector(".state").classList.contains("ok")).length;
    const failed = files.filter((r) => r.querySelector(".state").classList.contains("err")).length;
    const totalBytes = files.reduce((sum, r) => sum + Number(r.dataset.bytes || 0), 0);
    $("#job-stats").textContent = `${done} saved${failed ? ` · ${failed} failed` : ""} · ${fmtBytes(totalBytes)}`;
  }

  function finaliseJob(status, summary, error) {
    const fill = $("#job-progress");
    fill.classList.remove("is-indeterminate");

    if (status === "error") {
      fill.style.width = "100%";
      fill.style.background = "var(--err)";
      $("#job-title").textContent = "Download failed";
      $("#job-sub").textContent = error || "Unknown error";
    } else if (status === "cancelled") {
      $("#job-title").textContent = "Download cancelled";
      fill.style.width = "100%";
    } else {
      fill.style.width = "100%";
      $("#job-title").textContent = "Download complete";
      if (summary) {
        $("#job-sub").textContent = `${summary.downloaded} saved · ${summary.skipped} skipped · ${fmtBytes(summary.bytes)}`;
      } else {
        $("#job-sub").textContent = "finished";
      }
    }
    $("#job-cancel").classList.add("hidden");
    $("#job-close").classList.remove("hidden");
    $("#job-view").classList.remove("hidden");
    S.libraryDirty = true;
    S.libraryLoaded = false;
  }

  /* --------------------------------------------------------------- library */
  async function loadLibrary() {
    try {
      const data = await api("/api/library");
      S.libraryLoaded = true;
      S.libraryDirty = false;
      $("#lib-root").textContent = data.root || "";
      const list = $("#account-list");
      const count = data.accounts.length;
      $("#lib-count").hidden = count === 0;
      $("#lib-count").textContent = String(count);
      $("#lib-empty").classList.toggle("hidden", count > 0);
      list.innerHTML = data.accounts
        .map(
          (account) => `
          <li>
            <button type="button" data-account="${esc(account.username)}" class="${S.activeAccount === account.username ? "is-active" : ""}">
              <span class="acc-name">@${esc(account.username)}<small>${account.posts} post(s) · ${esc((account.scraped_at || "").slice(0, 10))}</small></span>
              <span class="acc-size">${fmtBytes(account.bytes)}</span>
            </button>
          </li>`
        )
        .join("");
      list.querySelectorAll("button").forEach((button) =>
        button.addEventListener("click", () => openAccount(button.dataset.account))
      );
      if (!S.activeAccount && count) openAccount(data.accounts[0].username);
    } catch (error) {
      toast(error.message);
    }
  }

  async function openAccount(username) {
    S.activeAccount = username;
    $$("#account-list button").forEach((button) =>
      button.classList.toggle("is-active", button.dataset.account === username)
    );
    try {
      const data = await api(`/api/library/${encodeURIComponent(username)}`);
      $("#lib-placeholder").classList.add("hidden");
      $("#lib-head").classList.remove("hidden");
      $("#lib-title").textContent = "@" + username;
      const profile = data.profile || {};
      $("#lib-meta").textContent = `${data.files.length} files · ${fmtBytes(data.bytes)} · scraped ${(data.scraped_at || "").slice(0, 10)}${profile.full_name ? " · " + profile.full_name : ""}`;
      $("#lib-metadata").href = `/downloads/${encodeURIComponent(username)}/metadata.json`;
      $("#lib-csv").href = `/downloads/${encodeURIComponent(username)}/posts.csv`;
      $("#lib-grid").innerHTML = data.files
        .map((file) => {
          const isVideo = file.type === "video";
          const src = file.url;
          return `
            <figure class="file-tile">
              ${isVideo ? `<video src="${esc(src)}" muted loop playsinline preload="metadata" controls></video>` : `<img src="${esc(src)}" alt="" loading="lazy">`}
              ${isVideo ? '<span class="badge video">▶ video</span>' : ""}
              <figcaption class="file-meta"><span>${esc(file.name)}</span><span>${fmtBytes(file.bytes)}</span></figcaption>
            </figure>`;
        })
        .join("");
    } catch (error) {
      toast(error.message);
    }
  }

  /* ----------------------------------------------------------------- modal */
  function openModal(index) {
    const posts = visiblePosts();
    if (index < 0 || index >= posts.length) return;
    S.modalIndex = index;
    const post = posts[index];
    const image = post.thumbnail_url || (post.media && post.media[0] && post.media[0].url) || "";
    const isVideo = post.media_type === "video";
    $("#modal-media").innerHTML = image
      ? `<img src="${esc(image)}" alt="" referrerpolicy="no-referrer">`
      : `<div style="padding:60px;color:var(--muted)">no preview</div>`;
    $("#modal-meta").innerHTML = `
      <span>${fmtDate(post.taken_at)}</span>
      <span>♥ ${fmtInt(post.like_count)}</span>
      <span>💬 ${fmtInt(post.comment_count)}</span>
      <span>${esc(post.media_type)}${post.media_count > 1 ? ` · ${post.media_count} files` : ""}</span>
      ${isVideo ? "<span>▶ contains video</span>" : ""}`;
    $("#modal-text").textContent = post.caption || "(no caption)";
    $("#modal-link").href = post.url || "#";
    $("#modal-download").onclick = () => {
      startDownload([post.shortcode], `@${S.profile ? S.profile.username : ""}`);
      closeModal();
    };
    $("#modal").classList.add("is-open");
    document.body.style.overflow = "hidden";
  }

  function closeModal() {
    $("#modal").classList.remove("is-open");
    document.body.style.overflow = "";
    S.modalIndex = -1;
  }

  function stepModal(delta) {
    const posts = visiblePosts();
    if (!posts.length) return;
    const next = (S.modalIndex + delta + posts.length) % posts.length;
    openModal(next);
  }

  /* ---------------------------------------------------------------- session */
  function sessionStatus(authenticated) {
    const el = $("#session");
    el.classList.toggle("is-auth", !!authenticated);
    el.querySelector(".session-text").textContent = authenticated ? "session cookie loaded" : "anonymous";
    el.title = authenticated
      ? "Requests use your exported cookies — higher limits, fewer blocks."
      : "No cookies: Instagram limits anonymous requests. Use --cookies for reliability.";
  }

  async function loadDemo() {
    showSkeletons(true);
    hideBanner();
    try {
      const data = await api("/api/demo");
      applyProfile(data.profile, data.posts, { demo: true });
      history.replaceState(null, "", "#/demo");
    } catch (error) {
      showSkeletons(false);
      showBanner("error", `<strong>Demo unavailable</strong>${esc(error.message)}`);
    }
  }

  /* ------------------------------------------------------------------ wiring */
  $("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    loadProfile($("#query").value);
  });

  $$(".tab").forEach((tab) => tab.addEventListener("click", () => setView(tab.dataset.view)));

  $$(".chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      $$(".chip").forEach((other) => other.classList.toggle("is-active", other === chip));
      S.filter = chip.dataset.filter;
      renderGrid();
    })
  );

  $("#select-all").addEventListener("change", selectAllVisible);
  $("#hero-demo").addEventListener("click", loadDemo);
  $("#banner").addEventListener("click", (event) => {
    if (event.target.closest('[data-action="demo"]')) loadDemo();
  });

  $("#download-all").addEventListener("click", () => startDownload(S.posts.map((post) => post.shortcode)));
  $("#toolbar-download").addEventListener("click", () => startDownload([...S.selected]));
  $("#sel-download").addEventListener("click", () => startDownload([...S.selected]));

  $("#copy-json").addEventListener("click", async () => {
    const posts = visiblePosts().map((post) => ({
      shortcode: post.shortcode,
      url: post.url,
      date: post.date,
      caption: post.caption,
      likes: post.like_count,
      comments: post.comment_count,
      media: (post.media || []).map((m) => m.url),
    }));
    try {
      await navigator.clipboard.writeText(JSON.stringify(posts, null, 2));
      toast(`Copied ${posts.length} post(s) as JSON`);
    } catch (_) {
      toast("Clipboard blocked by the browser");
    }
  });

  $("#job-cancel").addEventListener("click", async () => {
    if (!S.jobId) return;
    try {
      await api(`/api/jobs/${S.jobId}/cancel`, { method: "POST" });
      $("#job-sub").textContent = "cancelling…";
    } catch (error) {
      toast(error.message);
    }
  });
  $("#job-close").addEventListener("click", () => $("#job-panel").classList.remove("is-open"));
  $("#job-view").addEventListener("click", () => {
    setView("library");
    $("#job-panel").classList.remove("is-open");
  });

  $("#modal-close").addEventListener("click", closeModal);
  $("#modal-prev").addEventListener("click", () => stepModal(-1));
  $("#modal-next").addEventListener("click", () => stepModal(1));
  $("#modal").addEventListener("click", (event) => {
    if (event.target === $("#modal")) closeModal();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if ($("#modal").classList.contains("is-open")) closeModal();
      return;
    }
    if ($("#modal").classList.contains("is-open")) {
      if (event.key === "ArrowLeft") stepModal(-1);
      if (event.key === "ArrowRight") stepModal(1);
      return;
    }
    if (event.key === "/" && document.activeElement !== $("#query")) {
      event.preventDefault();
      $("#query").focus();
    }
  });

  // Deep links: #/@username or #/demo
  function fromHash() {
    const match = location.hash.match(/^#\/@?([\w.]+)$/);
    if (!match) return;
    if (match[1] === "demo") loadDemo();
    else {
      $("#query").value = match[1];
      loadProfile(match[1]);
    }
  }

  (async function init() {
    try {
      const health = await api("/api/health");
      sessionStatus(health.authenticated);
    } catch (_) {
      sessionStatus(false);
    }
    updateSelectionUI();
    fromHash();
    setTimeout(() => $("#query").focus({ preventScroll: true }), 400);
  })();
})();
