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

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wait = (ms) => new Promise((r) => setTimeout(r, reducedMotion ? 0 : ms));

  // ---- helpers ------------------------------------------------------------
  function showView(name) {
    Object.entries(views).forEach(([key, el]) => el.classList.toggle("active", key === name));
    progressTrack.hidden = name !== "quiz";
    headerStart.hidden = name !== "landing";
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
      showView("quiz");
      renderQuestion(data.question, data.progress, /*first=*/true);
    } catch (err) {
      showToast(err.message);
    }
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
    } catch (err) {
      showToast(err.message);
      choicesBox.querySelectorAll(".choice").forEach((b) => (b.disabled = false));
      btn.classList.remove("selected");
      answering = false;
      return;
    }

    if (data.done) {
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
      api(`/sessions/${sessionId}/report`).catch((err) => {
        showToast(err.message);
        return null;
      }),
      wait(1900),
    ]);

    clearInterval(analyzingTimer);
    if (!report) { showView("landing"); return; }
    renderReport(report);
  }

  // ---- report -------------------------------------------------------------
  function levelPercent(level) {
    return ((level - 1) / 9) * 100;
  }

  function animateCount(el, target, ms = 1300) {
    if (reducedMotion) { el.textContent = target.toFixed(1); return; }
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = (target * eased).toFixed(1);
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function renderReport(report) {
    const ability = report.ability;

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
      right.textContent = t.asked > 0 ? `${t.correct}/${t.asked} · level ${t.level.toFixed(1)} ` : "";

      const badge = document.createElement("span");
      badge.className = `badge ${t.verdict}`;
      badge.textContent = verdictLabel[t.verdict];
      right.appendChild(badge);

      top.append(name, right);
      row.appendChild(top);

      if (t.asked > 0) {
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

    // Switch view, then run entrance animations.
    showView("report");
    const reveals = views.report.querySelectorAll(".reveal");
    reveals.forEach((el) => el.classList.remove("shown"));

    requestAnimationFrame(() => {
      reveals.forEach((el, i) => setTimeout(() => el.classList.add("shown"), reducedMotion ? 0 : 90 * i));

      animateCount($("level-number"), ability.level);

      // Ability scale marker + CI band
      const lo = Math.max(1, Math.min(10, ability.ci_level[0]));
      const hi = Math.max(1, Math.min(10, ability.ci_level[1]));
      const ci = $("scale-ci");
      const marker = $("scale-marker");
      marker.style.left = "0%";
      ci.style.left = "0%";
      ci.style.width = "0%";
      setTimeout(() => {
        marker.style.left = levelPercent(ability.level) + "%";
        ci.style.left = levelPercent(lo) + "%";
        ci.style.width = Math.max(2, levelPercent(hi) - levelPercent(lo)) + "%";
      }, reducedMotion ? 0 : 250);

      // Topic bars
      setTimeout(() => {
        rows.querySelectorAll(".topic-bar-fill").forEach((f) => {
          f.style.width = f.dataset.width + "%";
        });
      }, reducedMotion ? 0 : 500);

      renderRadar(assessed);
    });
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
          backgroundColor: "rgba(79, 70, 229, 0.14)",
          borderColor: "#4F46E5",
          borderWidth: 2,
          pointBackgroundColor: assessed.map((t) => (t.verdict === "focus" ? "#F59E0B" : "#4F46E5")),
          pointRadius: 4,
          pointHoverRadius: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: reducedMotion ? false : { duration: 1100, easing: "easeOutQuart" },
        scales: {
          r: {
            min: 0,
            max: 10,
            ticks: { stepSize: 2, display: false },
            grid: { color: "rgba(28, 32, 51, 0.08)" },
            angleLines: { color: "rgba(28, 32, 51, 0.08)" },
            pointLabels: {
              font: { family: "Inter", size: 11, weight: "600" },
              color: "#5A5F76",
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
})();
