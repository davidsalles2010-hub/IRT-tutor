/* MathLens frontend — landing → adaptive quiz → animated report. */

(() => {
  "use strict";

  const API = "/api";
  const LETTERS = ["A", "B", "C", "D"];

  // ---- elements -----------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const views = {
    landing: $("view-landing"),
    quiz: $("view-quiz"),
    analyzing: $("view-analyzing"),
    report: $("view-report"),
    auth: $("view-auth"),
  };
  const progressTrack = $("progress-track");
  const progressFill = $("progress-fill");
  const headerStart = $("header-start");
  const questionCard = $("question-card");
  const questionText = $("question-text");
  const choicesBox = $("choices");
  const quizCount = $("quiz-count");
  const quizTopic = $("quiz-topic");
  const toast = $("toast");

  // ---- state --------------------------------------------------------------
  let sessionId = null;
  let currentQuestion = null;
  let answering = false;
  let radarChart = null;
  let analyzingTimer = null;
  // Client-held answer history. The free hosting tier restarts the server
  // when it idles, wiping its in-memory sessions — with this list we can ask
  // the API to rebuild the session and resume instead of losing progress.
  let answerHistory = [];

  const log = (...args) => console.info("MathLens:", ...args);

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wait = (ms) => new Promise((r) => setTimeout(r, reducedMotion ? 0 : ms));

  // ---- helpers ------------------------------------------------------------
  let currentView = "landing";

  function showView(name) {
    currentView = name;
    Object.entries(views).forEach(([key, el]) => el.classList.toggle("active", key === name));
    progressTrack.hidden = name !== "quiz";
    updateHeader();
    window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
  }

  function showToast(message, ms = 4200) {
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => { toast.hidden = true; }, ms);
  }

  async function api(path, options) {
    let res;
    try {
      res = await fetch(API + path, options);
    } catch {
      throw new Error("Can't reach the server — check your connection and try again.");
    }
    if (!res.ok) {
      let detail = "Something went wrong.";
      try { detail = (await res.json()).detail || detail; } catch { /* noop */ }
      throw new Error(detail);
    }
    return res.json();
  }

  // ---- quiz flow ----------------------------------------------------------
  async function startDiagnostic() {
    try {
      const data = await api("/sessions", { method: "POST" });
      sessionId = data.session_id;
      answerHistory = [];
      log("session started", sessionId);
      showView("quiz");
      renderQuestion(data.question, data.progress, /*first=*/true);
    } catch (err) {
      showToast(err.message);
    }
  }

  // ---- session restore (free-tier server naps wipe in-memory sessions) ----
  const isSessionLost = (err) => /not found or expired/i.test(err && err.message || "");

  async function restoreSession() {
    let lastErr = null;
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const data = await api("/sessions/restore", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answers: answerHistory }),
        });
        sessionId = data.session_id;
        log("session restored", sessionId, "after", answerHistory.length, "answers");
        return data;
      } catch (err) {
        lastErr = err;
        log("restore attempt", attempt, "failed:", err.message);
        await wait(2500); // server may still be waking up
      }
    }
    throw lastErr || new Error("Could not restore the session.");
  }

  function updateProgress(progress, done = false) {
    const pct = done ? 100 : Math.min(100, (progress.answered / progress.max_questions) * 100);
    progressFill.style.width = pct + "%";
  }

  function renderQuestion(question, progress, first = false) {
    currentQuestion = question;
    answering = false;

    quizCount.textContent = `Question ${question.number} of up to ${progress.max_questions}`;
    quizTopic.textContent = question.topic_label;
    updateProgress(progress);

    questionText.textContent = question.text;
    choicesBox.innerHTML = "";
    question.choices.forEach((choice, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice";
      btn.dataset.index = String(i);
      btn.style.setProperty("--i", String(i));

      const letter = document.createElement("span");
      letter.className = "choice-letter";
      letter.textContent = LETTERS[i] || String(i + 1);

      const label = document.createElement("span");
      label.textContent = choice;

      btn.append(letter, label);
      btn.addEventListener("click", () => submitAnswer(i, btn));
      choicesBox.appendChild(btn);
    });

    if (!first) {
      questionCard.classList.add("entering");
      requestAnimationFrame(() => {
        requestAnimationFrame(() => questionCard.classList.remove("entering"));
      });
    }
  }

  async function submitAnswer(index, btn) {
    if (answering || !currentQuestion) return;
    answering = true;

    choicesBox.querySelectorAll(".choice").forEach((b) => (b.disabled = true));
    btn.classList.add("selected");
    await wait(240);

    let data;
    try {
      data = await api(`/sessions/${sessionId}/answers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_id: currentQuestion.id, choice_index: index }),
      });
      answerHistory.push({ question_id: currentQuestion.id, choice_index: index });
    } catch (err) {
      if (isSessionLost(err)) {
        // Server restarted mid-quiz: rebuild the session and carry on.
        try {
          const restored = await restoreSession();
          showToast("Reconnected after a short server nap — picking up where you left off.");
          if (restored.done) { await showReportFlow(); return; }
          questionCard.classList.add("leaving");
          await wait(260);
          questionCard.classList.remove("leaving");
          renderQuestion(restored.question, restored.progress);
          return;
        } catch (restoreErr) {
          showToast("The server is waking up — give it a few seconds and answer again. Your progress is safe.");
          choicesBox.querySelectorAll(".choice").forEach((b) => (b.disabled = false));
          btn.classList.remove("selected");
          answering = false;
          return;
        }
      }
      showToast(err.message);
      choicesBox.querySelectorAll(".choice").forEach((b) => (b.disabled = false));
      btn.classList.remove("selected");
      answering = false;
      return;
    }

    if (data.done) {
      log("diagnostic complete after", answerHistory.length, "answers");
      updateProgress(data.progress, true);
      await wait(350);
      await showReportFlow();
      return;
    }

    questionCard.classList.add("leaving");
    await wait(260);
    questionCard.classList.remove("leaving");
    renderQuestion(data.question, data.progress);
  }

  document.addEventListener("keydown", (e) => {
    if (!views.quiz.classList.contains("active") || answering) return;
    const key = e.key.toUpperCase();
    let idx = LETTERS.indexOf(key);
    if (idx === -1 && /^[1-4]$/.test(key)) idx = Number(key) - 1;
    if (idx === -1) return;
    const btn = choicesBox.querySelector(`.choice[data-index="${idx}"]`);
    if (btn) btn.click();
  });

  // ---- analyzing interstitial ----------------------------------------------
  const ANALYZING_NOTES = [
    "Estimating ability from the full answer pattern",
    "Weighing each question by its difficulty",
    "Mapping strengths and gaps by topic",
  ];

  async function fetchReportResilient() {
    try {
      return await api(`/sessions/${sessionId}/report`);
    } catch (err) {
      if (!isSessionLost(err) || answerHistory.length === 0) throw err;
      log("session lost at report time — restoring");
      await restoreSession();
      return api(`/sessions/${sessionId}/report`);
    }
  }

  async function showReportFlow() {
    showView("analyzing");
    let i = 0;
    const note = $("analyzing-note");
    note.textContent = ANALYZING_NOTES[0];
    analyzingTimer = setInterval(() => {
      i = (i + 1) % ANALYZING_NOTES.length;
      note.textContent = ANALYZING_NOTES[i];
    }, 1100);

    const [report] = await Promise.all([
      fetchReportResilient().catch((err) => {
        log("report fetch failed:", err.message);
        showToast(err.message);
        return null;
      }),
      wait(1900),
    ]);

    clearInterval(analyzingTimer);
    if (!report) {
      // Never strand the student on a blank screen: explain and offer retry.
      $("analyzing-note").textContent = "";
      const shell = document.querySelector(".analyzing-shell");
      shell.querySelector("h2").textContent = "Couldn't load the report";
      const p = document.createElement("p");
      p.textContent = "The server may have been napping (free hosting). Your answers are safe on this page.";
      const retry = document.createElement("button");
      retry.className = "btn btn-primary";
      retry.type = "button";
      retry.style.marginTop = "14px";
      retry.textContent = "Try loading the report again";
      retry.addEventListener("click", () => { retry.remove(); p.remove(); shell.querySelector("h2").textContent = "Building the report…"; showReportFlow(); });
      shell.append(p, retry);
      return;
    }
    log("report received:", report.n_questions, "questions, level", report.ability && report.ability.level);
    renderReport(report);
  }

  // ---- report -------------------------------------------------------------
  function levelPercent(level) {
    return ((level - 1) / 9) * 100;
  }

  function animateCount(el, target, ms = 1300) {
    // Correctness first: the real value is set synchronously, so the number
    // is right even if animation frames never fire (hidden tab, low power).
    el.textContent = target.toFixed(1);
    if (reducedMotion || document.hidden) return;
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = (target * (t < 1 ? eased : 1)).toFixed(1);
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function renderReport(report) {
    try {
      renderReportInner(report);
      log("report rendered in full");
    } catch (err) {
      // Fail-safe: never leave the student staring at a blank report.
      console.error("MathLens: report render failed —", err);
      showView("report");
      views.report.querySelectorAll(".reveal").forEach((el) => { el.classList.remove("pre"); el.classList.add("shown"); });
      try {
        const a = report && report.ability;
        if (a && typeof a.level === "number") {
          $("level-number").textContent = a.level.toFixed(1);
          $("level-band").textContent = a.band || "";
          $("level-desc").textContent = a.band_description || "";
        }
      } catch (_) { /* keep whatever rendered */ }
      showToast("Part of the report failed to display. Core results are shown — please retake if anything looks off.");
    }
  }

  function renderReportInner(report) {
    const ability = report.ability;
    report.topics = report.topics || [];
    report.review = report.review || [];
    ability.ci_level = ability.ci_level || [1, 10];

    // Hero numbers
    $("level-band").textContent = ability.band;
    $("level-desc").textContent = ability.band_description;
    $("scale-note").textContent =
      `Based on ${report.n_questions} adaptive questions (${report.n_correct} correct). ` +
      `We're 95% confident the true level is between ${ability.ci_level[0].toFixed(1)} and ${ability.ci_level[1].toFixed(1)}.`;

    // Topic rows + radar data
    const assessed = report.topics.filter((t) => t.asked > 0 && t.level !== null);
    const rows = $("topic-rows");
    rows.innerHTML = "";
    const verdictLabel = { strength: "Strength", focus: "Focus area", on_track: "On track", not_assessed: "Not assessed" };

    report.topics.forEach((t) => {
      const row = document.createElement("div");
      row.className = "topic-row";
      const top = document.createElement("div");
      top.className = "topic-row-top";

      const name = document.createElement("span");
      name.className = "topic-name";
      name.textContent = t.label;

      const right = document.createElement("span");
      right.className = "topic-detail";
      right.textContent = t.asked > 0 && t.level != null ? `${t.correct}/${t.asked} · level ${t.level.toFixed(1)} ` : "";

      const badge = document.createElement("span");
      badge.className = `badge ${t.verdict}`;
      badge.textContent = verdictLabel[t.verdict];
      right.appendChild(badge);

      top.append(name, right);
      row.appendChild(top);

      if (t.asked > 0 && t.level != null) {
        const bar = document.createElement("div");
        bar.className = "topic-bar";
        const fill = document.createElement("div");
        fill.className = "topic-bar-fill" + (t.verdict === "focus" ? " focus" : "");
        fill.dataset.width = String((t.level / 10) * 100);
        bar.appendChild(fill);
        row.appendChild(bar);
      }
      rows.appendChild(row);
    });

    // Focus panel
    const focusList = $("focus-list");
    focusList.innerHTML = "";
    const focus = report.topics.filter((t) => t.verdict === "focus");
    if (focus.length === 0 && ability.level < 5 && assessed.length >= 2) {
      // Uniformly low: no *relative* gap, but fundamentals need broad work.
      const intro = document.createElement("p");
      intro.className = "panel-note";
      intro.textContent =
        "No single topic lags behind the others — the fastest wins will come from broad practice. Start with the foundations:";
      focusList.appendChild(intro);
      [...assessed].sort((a, b) => a.level - b.level).slice(0, 2).forEach((t) => {
        const card = document.createElement("div");
        card.className = "focus-card";
        const h = document.createElement("h3");
        h.textContent = `${t.label} — level ${t.level.toFixed(1)}`;
        const p = document.createElement("p");
        p.textContent = t.tip || "Steady practice here builds the base everything else stands on.";
        card.append(h, p);
        focusList.appendChild(card);
      });
    } else if (focus.length === 0) {
      const ok = document.createElement("div");
      ok.className = "focus-none";
      ok.textContent = "No standout gaps this session — skills are balanced at this level. Keep practicing evenly, then retake to track growth.";
      focusList.appendChild(ok);
    } else {
      focus.forEach((t) => {
        const card = document.createElement("div");
        card.className = "focus-card";
        const h = document.createElement("h3");
        h.textContent = `${t.label} — level ${t.level.toFixed(1)}`;
        const p = document.createElement("p");
        p.textContent = t.tip || "Targeted practice here will lift the overall level fastest.";
        card.append(h, p);
        focusList.appendChild(card);
      });
    }

    // Review accordion
    const reviewList = $("review-list");
    reviewList.innerHTML = "";
    const missed = report.review.filter((r) => !r.correct).length;
    $("review-summary").textContent = missed
      ? `${missed} of ${report.review.length} answers were incorrect — those are worth a look. An adaptive test is designed to find questions hard enough to miss.`
      : `A perfect run — every question answered correctly.`;

    report.review.forEach((r) => {
      const details = document.createElement("details");
      details.className = "review-item";
      if (!r.correct) details.open = false;

      const summary = document.createElement("summary");
      const status = document.createElement("span");
      status.className = "review-status " + (r.correct ? "right" : "wrong");
      status.textContent = r.correct ? "✓" : "✕";
      const q = document.createElement("span");
      q.className = "review-q";
      q.textContent = `${r.number}. ${r.text}`;
      const topic = document.createElement("span");
      topic.className = "review-topic";
      topic.textContent = r.topic_label;
      summary.append(status, q, topic);

      const body = document.createElement("div");
      body.className = "review-body";
      const full = document.createElement("p");
      full.textContent = r.text;
      body.appendChild(full);

      if (!r.correct) {
        const yours = document.createElement("p");
        yours.innerHTML = `Your answer: <span class="review-your"></span>`;
        yours.querySelector("span").textContent = r.choices[r.your_index];
        body.appendChild(yours);
      }
      const correct = document.createElement("p");
      correct.innerHTML = `Correct answer: <span class="review-correct"></span>`;
      correct.querySelector("span").textContent = r.choices[r.correct_index];
      body.appendChild(correct);

      if (r.explanation) {
        const expl = document.createElement("p");
        expl.className = "review-expl";
        expl.textContent = r.explanation;
        body.appendChild(expl);
      }

      details.append(summary, body);
      reviewList.appendChild(details);
    });

    $("report-disclaimer").textContent = report.disclaimer;
    $("save-hint").hidden = !!currentUser;

    // Switch view and render everything SYNCHRONOUSLY. Animations are a
    // bonus layered on top — never a prerequisite for seeing the content
    // (rAF/timers are suspended in background tabs, which previously left
    // the report invisible with the level stuck at 0.0).
    showView("report");
    const reveals = [...views.report.querySelectorAll(".reveal")];
    const animate = !reducedMotion && !document.hidden;

    reveals.forEach((el) => { el.classList.remove("shown", "pre"); el.style.transitionDelay = ""; });
    if (animate) {
      reveals.forEach((el, i) => {
        el.classList.add("pre");
        el.style.transitionDelay = (90 * i) + "ms";
      });
      void views.report.offsetHeight; // reflow so the transition runs
    }
    reveals.forEach((el) => el.classList.add("shown"));
    setTimeout(() => reveals.forEach((el) => { el.classList.remove("pre"); el.style.transitionDelay = ""; }), 2000);

    animateCount($("level-number"), ability.level);

    // Ability scale marker + CI band — final positions set synchronously;
    // CSS transitions animate them when the tab is visible.
    const lo = Math.max(1, Math.min(10, ability.ci_level[0]));
    const hi = Math.max(1, Math.min(10, ability.ci_level[1]));
    const ci = $("scale-ci");
    const marker = $("scale-marker");
    if (animate) {
      marker.style.left = "0%"; ci.style.left = "0%"; ci.style.width = "0%";
      void marker.offsetWidth;
    }
    marker.style.left = levelPercent(ability.level) + "%";
    ci.style.left = levelPercent(lo) + "%";
    ci.style.width = Math.max(2, levelPercent(hi) - levelPercent(lo)) + "%";

    // Topic bars
    rows.querySelectorAll(".topic-bar-fill").forEach((f) => {
      if (animate) void f.offsetWidth;
      f.style.width = f.dataset.width + "%";
    });

    renderRadar(assessed);
  }

  function renderRadar(assessed) {
    const canvas = $("radar-chart");
    if (radarChart) { radarChart.destroy(); radarChart = null; }
    if (typeof Chart === "undefined" || assessed.length < 3) {
      canvas.parentElement.innerHTML = "<p class='panel-note'>Skill map unavailable for this session.</p>";
      return;
    }

    radarChart = new Chart(canvas.getContext("2d"), {
      type: "radar",
      data: {
        labels: assessed.map((t) => t.label.replace(" & ", " & ")),
        datasets: [{
          label: "Level",
          data: assessed.map((t) => t.level),
          fill: true,
          backgroundColor: "rgba(30, 92, 65, 0.14)",
          borderColor: "#1E5C41",
          borderWidth: 2,
          pointBackgroundColor: assessed.map((t) => (t.verdict === "focus" ? "#C4622D" : "#1E5C41")),
          pointRadius: 4,
          pointHoverRadius: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: (reducedMotion || document.hidden) ? false : { duration: 1100, easing: "easeOutQuart" },
        scales: {
          r: {
            min: 0,
            max: 10,
            ticks: { stepSize: 2, display: false },
            grid: { color: "rgba(30, 43, 38, 0.08)" },
            angleLines: { color: "rgba(30, 43, 38, 0.08)" },
            pointLabels: {
              font: { family: "Instrument Sans", size: 11, weight: "600" },
              color: "#5A6A61",
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => ` Level ${ctx.parsed.r.toFixed(1)} / 10`,
            },
          },
        },
      },
    });
  }

  // ==========================================================================
  // Auth
  // ==========================================================================
  let currentUser = null;
  let appConfig = { google_client_id: null, email_enabled: false };

  function updateHeader() {
    const inQuiz = currentView === "quiz" || currentView === "analyzing";
    headerStart.hidden = inQuiz || currentView !== "landing";
    $("header-login").hidden = !!currentUser || inQuiz || currentView === "auth";
    $("account-wrap").hidden = !currentUser;
    if (currentUser) {
      const initials = (currentUser.name || currentUser.email || "?")
        .split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
      $("avatar-dot").textContent = initials;
      $("chip-name").textContent = (currentUser.name || "").split(/\s+/)[0];
      $("menu-name").textContent = currentUser.name || "";
      $("menu-email").textContent = currentUser.email || "";
      $("menu-role").textContent = currentUser.role || "student";
    }
    $("verify-banner").hidden = !(
      currentUser && currentUser.has_password && !currentUser.email_verified && appConfig.email_enabled
    );
  }

  const authError = (msg) => { const el = $("auth-error"); el.textContent = msg || ""; el.hidden = !msg; };
  const authOk = (msg) => { const el = $("auth-ok"); el.textContent = msg || ""; el.hidden = !msg; };

  function switchAuthTab(which) {
    $("tab-login").classList.toggle("active", which === "login");
    $("tab-signup").classList.toggle("active", which === "signup");
    $("form-login").hidden = which !== "login";
    $("form-signup").hidden = which !== "signup";
    $("auth-title").textContent = which === "login" ? "Welcome back" : "Create your account";
    $("auth-sub").textContent = which === "login"
      ? "Log in to save results and track progress over time."
      : "Free while in preview. Results from future diagnostics get saved to your profile.";
    authError(null); authOk(null);
  }

  function showAuth(which = "login") {
    $("auth-main").hidden = false;
    $("auth-forgot").hidden = true;
    $("auth-reset").hidden = true;
    switchAuthTab(which);
    showView("auth");
  }

  async function refreshMe() {
    try {
      const data = await api("/auth/me");
      currentUser = data.user;
    } catch {
      currentUser = null;
    }
    updateHeader();
  }

  async function handleLogin(e) {
    e.preventDefault();
    authError(null);
    const btn = $("login-submit");
    btn.disabled = true;
    try {
      await api("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: $("login-email").value.trim(), password: $("login-password").value }),
      });
      await refreshMe();
      log("logged in as", currentUser && currentUser.email);
      showToast(`Welcome back${currentUser && currentUser.name ? ", " + currentUser.name.split(/\s+/)[0] : ""}!`);
      showView("landing");
    } catch (err) {
      authError(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function handleSignup(e) {
    e.preventDefault();
    authError(null);
    const btn = $("signup-submit");
    btn.disabled = true;
    try {
      await api("/auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: $("signup-name").value.trim(),
          email: $("signup-email").value.trim(),
          password: $("signup-password").value,
        }),
      });
      await refreshMe();
      log("signed up as", currentUser && currentUser.email);
      showToast("Account created — welcome to MathLens!");
      showView("landing");
    } catch (err) {
      authError(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function handleForgot(e) {
    e.preventDefault();
    const err = $("forgot-error"), ok = $("forgot-ok");
    err.hidden = true; ok.hidden = true;
    try {
      await api("/auth/forgot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: $("forgot-email").value.trim() }),
      });
      ok.textContent = "If an account exists for that email, a reset link is on its way.";
      ok.hidden = false;
    } catch (e2) {
      err.textContent = e2.message;
      err.hidden = false;
    }
  }

  async function handleReset(e) {
    e.preventDefault();
    const err = $("reset-error");
    err.hidden = true;
    const token = new URLSearchParams(location.search).get("reset");
    try {
      await api("/auth/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password: $("reset-password").value }),
      });
      history.replaceState(null, "", location.pathname);
      showAuth("login");
      authOk("Password updated — log in with your new password.");
    } catch (e2) {
      err.textContent = e2.message;
      err.hidden = false;
    }
  }

  async function handleLogout() {
    try { await api("/auth/logout", { method: "POST" }); } catch { /* session may already be gone */ }
    currentUser = null;
    $("account-menu").hidden = true;
    updateHeader();
    showToast("Logged out.");
    showView("landing");
  }

  function initGoogle() {
    if (!appConfig.google_client_id) return;
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.onload = () => {
      if (!window.google || !google.accounts) return;
      google.accounts.id.initialize({
        client_id: appConfig.google_client_id,
        callback: async (resp) => {
          try {
            await api("/auth/google", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ credential: resp.credential }),
            });
            await refreshMe();
            showToast(`Welcome${currentUser && currentUser.name ? ", " + currentUser.name.split(/\s+/)[0] : ""}!`);
            showView("landing");
          } catch (err) {
            authError(err.message);
          }
        },
      });
      google.accounts.id.renderButton($("google-slot"), { theme: "outline", size: "large", width: 320, text: "continue_with" });
      $("google-area").hidden = false;
    };
    document.head.appendChild(s);
  }

  function initAuth() {
    $("tab-login").addEventListener("click", () => switchAuthTab("login"));
    $("tab-signup").addEventListener("click", () => switchAuthTab("signup"));
    $("form-login").addEventListener("submit", handleLogin);
    $("form-signup").addEventListener("submit", handleSignup);
    $("form-forgot").addEventListener("submit", handleForgot);
    $("form-reset").addEventListener("submit", handleReset);
    $("header-login").addEventListener("click", () => showAuth("login"));
    $("save-hint-login").addEventListener("click", () => showAuth("signup"));
    $("menu-logout").addEventListener("click", handleLogout);
    $("menu-retake").addEventListener("click", () => { $("account-menu").hidden = true; startDiagnostic(); });
    $("show-forgot").addEventListener("click", () => {
      $("auth-main").hidden = true; $("auth-forgot").hidden = false;
    });
    $("back-to-login").addEventListener("click", () => {
      $("auth-forgot").hidden = true; $("auth-main").hidden = false; switchAuthTab("login");
    });
    $("resend-verify").addEventListener("click", async () => {
      try { await api("/auth/resend-verification", { method: "POST" }); showToast("Verification email sent."); }
      catch (err) { showToast(err.message); }
    });

    // account menu open/close
    const chip = $("account-chip");
    const menu = $("account-menu");
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      chip.setAttribute("aria-expanded", String(!menu.hidden));
    });
    document.addEventListener("click", (e) => {
      if (!menu.hidden && !menu.contains(e.target) && e.target !== chip) menu.hidden = true;
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.hidden = true; });
  }

  async function bootstrap() {
    try { appConfig = await api("/config"); } catch { /* defaults */ }
    await refreshMe();
    initGoogle();
    // Password-reset deep link: /?reset=TOKEN
    if (new URLSearchParams(location.search).get("reset")) {
      $("auth-main").hidden = true;
      $("auth-forgot").hidden = true;
      $("auth-reset").hidden = false;
      showView("auth");
    }
    // Email-verification deep link: /?verify=TOKEN
    const verifyToken = new URLSearchParams(location.search).get("verify");
    if (verifyToken) {
      try {
        await api("/auth/verify-email", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: verifyToken }),
        });
        showToast("Email verified — thanks!");
        await refreshMe();
      } catch (err) {
        showToast(err.message);
      }
      history.replaceState(null, "", location.pathname);
    }
  }

  // ---- landing scroll reveals (safe: visible by default) -------------------
  function initRise() {
    const els = [...document.querySelectorAll(".rise")];
    if (reducedMotion || !("IntersectionObserver" in window) || els.length === 0) return;
    const below = els.filter((el) => el.getBoundingClientRect().top > window.innerHeight * 0.92);
    below.forEach((el) => el.classList.add("pre"));
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12 });
    below.forEach((el) => io.observe(el));
    // Safety: never leave anything hidden.
    setTimeout(() => below.forEach((el) => { el.classList.add("in"); }), 5000);
  }

  // ---- wiring -------------------------------------------------------------
  ["hero-start", "footer-start", "header-start"].forEach((id) => {
    $(id).addEventListener("click", startDiagnostic);
  });

  $("retake").addEventListener("click", startDiagnostic);
  $("print-report").addEventListener("click", () => window.print());
  $("brand-link").addEventListener("click", (e) => {
    e.preventDefault();
    if (!views.quiz.classList.contains("active")) showView("landing");
  });

  window.addEventListener("scroll", () => {
    $("site-header").classList.toggle("scrolled", window.scrollY > 8);
  }, { passive: true });

  initAuth();
  initRise();
  bootstrap();
})();
