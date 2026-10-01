(() => {
  const questionData = document.getElementById("coding-questions");
  const starterData = document.getElementById("coding-starters");
  const questionList = questionData ? JSON.parse(questionData.textContent) : [];
  const starters = starterData ? JSON.parse(starterData.textContent) : {};
  const questionById = new Map(questionList.map(question => [question.id, question]));
  const editor = document.querySelector("[data-code-editor]");
  const languageSelect = document.querySelector("[data-language]");
  const outputResults = document.querySelector("[data-output-results]");
  const outputStatus = document.querySelector("[data-output-status]");
  const solved = new Set(questionList.filter(question =>
    document.querySelector(`[data-solved-indicator="${question.id}"]:not([hidden])`)
  ).map(question => question.id));
  const codeByQuestion = new Map();
  let activeQuestion = questionById.get(new URLSearchParams(location.search).get("question")) || questionList[0];
  let busy = false;

  if (!activeQuestion || !editor || !languageSelect) return;

  function saveCurrentCode() {
    if (activeQuestion) codeByQuestion.set(`${activeQuestion.id}:${languageSelect.value}`, editor.value);
  }

  function renderExamples(question) {
    const container = document.querySelector("[data-problem-examples]");
    container.replaceChildren();
    question.tests.forEach((example, index) => {
      const card = document.createElement("div");
      const title = document.createElement("strong");
      const sample = document.createElement("div");
      const input = document.createElement("pre");
      const output = document.createElement("pre");
      card.className = "example-case";
      title.textContent = `Test case ${index + 1}`;
      sample.className = "example-io";
      input.textContent = example.input;
      output.textContent = example.output;
      input.setAttribute("aria-label", "Example input");
      output.setAttribute("aria-label", "Expected output");
      sample.append(input, output);
      card.append(title, sample);
      container.append(card);
    });
  }

  function renderQuestion(question) {
    saveCurrentCode();
    activeQuestion = question;
    document.querySelector("[data-problem-topic]").textContent = question.topic;
    document.querySelector("[data-problem-companies]").textContent =
      `Company practice tags: ${question.companies.join(", ")}`;
    document.querySelector("[data-problem-title]").textContent = question.title;
    document.querySelector("[data-problem-difficulty]").textContent =
      question.difficulty[0].toUpperCase() + question.difficulty.slice(1);
    document.querySelector("[data-problem-difficulty]").className =
      `difficulty-badge difficulty-${question.difficulty}`;
    document.querySelector("[data-problem-statement]").textContent = question.statement;
    renderExamples(question);
    const constraints = document.querySelector("[data-problem-constraints]");
    constraints.replaceChildren();
    question.constraints.forEach(constraint => {
      const item = document.createElement("li");
      item.textContent = constraint;
      constraints.append(item);
    });
    editor.value = codeByQuestion.get(`${question.id}:${languageSelect.value}`) ?? starters[languageSelect.value] ?? "";
    document.querySelectorAll("[data-question-link]").forEach(button => {
      const isCurrent = button.dataset.questionLink === question.id;
      button.setAttribute("aria-current", String(isCurrent));
      button.classList.toggle("is-current", isCurrent);
    });
    outputResults.textContent = "Run a sample test to see your output.";
    outputStatus.textContent = solved.has(question.id) ? "Solved" : "Ready when you are";
    document.querySelector(".coding-editor").classList.remove("has-result", "has-failure");
    history.replaceState(null, "", `${location.pathname}?question=${encodeURIComponent(question.id)}`);
  }

  document.querySelectorAll("[data-question-link]").forEach(button => {
    button.addEventListener("click", () => {
      if (!busy) renderQuestion(questionById.get(button.dataset.questionLink));
    });
  });

  languageSelect.addEventListener("change", () => {
    saveCurrentCode();
    editor.value = codeByQuestion.get(`${activeQuestion.id}:${languageSelect.value}`) ?? starters[languageSelect.value] ?? "";
  });
  editor.addEventListener("input", saveCurrentCode);
  document.querySelector("[data-reset-code]").addEventListener("click", () => {
    editor.value = starters[languageSelect.value] ?? "";
    saveCurrentCode();
  });

  function showResults(results) {
    outputResults.replaceChildren();
    results.forEach((result, index) => {
      const card = document.createElement("article");
      const title = document.createElement("strong");
      const status = document.createElement("span");
      card.className = `test-result ${result.passed ? "test-passed" : "test-failed"}`;
      title.textContent = `Test ${index + 1}`;
      status.textContent = result.passed ? "Passed" : "Needs another look";
      card.append(title, status);
      if (!result.passed) {
        const detail = document.createElement("pre");
        detail.textContent = result.stderr || `Expected:\n${result.expected}\n\nReceived:\n${result.actual}`;
        card.append(detail);
      }
      outputResults.append(card);
    });
  }

  async function execute(action) {
    if (busy) return;
    busy = true;
    const runButton = document.querySelector("[data-run-code]");
    const submitButton = document.querySelector("[data-submit-code]");
    runButton.disabled = true;
    submitButton.disabled = true;
    outputStatus.textContent = action === "submit" ? "Checking all examples…" : "Running sample…";
    document.querySelector(".coding-editor").classList.remove("has-result", "has-failure");
    try {
      const response = await fetch("/api/coding/execute", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": window.codingCsrfToken
        },
        body: JSON.stringify({
          question_id: activeQuestion.id,
          language: languageSelect.value,
          code: editor.value,
          action
        })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "The sandbox request failed.");
      showResults(payload.results);
      const passed = payload.results.every(result => result.passed);
      document.querySelector(".coding-editor").classList.add(passed ? "has-result" : "has-failure");
      outputStatus.textContent = action === "submit"
        ? (passed ? "All test cases passed" : "Some test cases need attention")
        : (passed ? "Sample passed" : "Sample needs attention");
      if (action === "submit" && passed) {
        solved.add(activeQuestion.id);
        document.querySelector(`[data-solved-indicator="${activeQuestion.id}"]`).hidden = false;
        const completeCount = solved.size;
        document.querySelector("[data-completed-count]").textContent = String(completeCount);
        document.querySelector("[data-coding-score]").textContent = Number(
          (completeCount / questionList.length * 10).toFixed(1)
        ).toFixed(1);
        document.querySelector("[data-coding-progress]").style.width =
          `${Math.round(completeCount / questionList.length * 100)}%`;
        if (payload.session_complete) outputStatus.textContent = "Session complete · 50 XP earned";
      }
      if (!passed) {
        const errors = payload.results.filter(result => !result.passed && result.stderr).map(result => result.stderr);
        if (errors.length) outputStatus.textContent = errors[0].slice(0, 160);
      }
    } catch (error) {
      outputResults.textContent = error.message;
      outputStatus.textContent = "Unable to run";
      document.querySelector(".coding-editor").classList.add("has-failure");
    } finally {
      busy = false;
      runButton.disabled = false;
      submitButton.disabled = false;
    }
  }

  document.querySelector("[data-run-code]").addEventListener("click", () => execute("run"));
  document.querySelector("[data-submit-code]").addEventListener("click", () => execute("submit"));

  let seconds = 30 * 60;
  const timer = document.querySelector("[data-coding-timer]");
  window.setInterval(() => {
    seconds = Math.max(0, seconds - 1);
    timer.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    timer.classList.toggle("timer-low", seconds <= 5 * 60);
  }, 1000);

  renderQuestion(activeQuestion);
})();
