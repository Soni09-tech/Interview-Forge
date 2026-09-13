const $ = (id) => document.getElementById(id);
let csrf = "", authMode = "login", roles = [], roleNames = [], companies = [];
const themeToggle = document.querySelector("[data-theme-toggle]");
function updateThemeToggle() {
  const light = document.documentElement.dataset.theme === "light";
  if (!themeToggle) return;
  themeToggle.querySelector(".theme-icon").textContent = light ? "☾" : "☀";
  themeToggle.querySelector(".theme-label").textContent = light ? "Dark" : "Light";
  themeToggle.setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to light theme");
  themeToggle.setAttribute("aria-pressed", String(light));
}
if (themeToggle) themeToggle.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("interviewforge-theme", next);
  updateThemeToggle();
});
updateThemeToggle();
function escapeHTML(value) {
  const element = document.createElement("div");
  element.textContent = String(value);
  return element.innerHTML;
}
document.querySelectorAll("[data-password-toggle]").forEach(button => {
  button.addEventListener("click", () => {
    const input = button.parentElement.querySelector("input");
    const visible = input.type === "text";
    input.type = visible ? "password" : "text";
    button.textContent = visible ? button.dataset.showLabel : button.dataset.hideLabel;
    button.setAttribute("aria-label", visible ? button.dataset.showLabel : button.dataset.hideLabel);
    button.setAttribute("aria-pressed", String(!visible));
  });
});
async function api(url, options = {}) {
  options.headers = options.headers || {}; if (csrf) options.headers["X-CSRF-Token"] = csrf;
  const r = await fetch(url, options), data = await r.json().catch(() => ({}));
  if (!r.ok) throw Error(data.error || "Something went wrong"); return data;
}
function flash(message, error = false) { const el = $("flash"); el.textContent = message; el.className = `flash ${error ? "error" : ""}`; el.hidden = false; setTimeout(() => el.hidden = true, 5000); }
function showPage(name) { document.querySelectorAll(".page").forEach(p => p.hidden = p.id !== `page-${name}`); document.querySelectorAll(".nav-item[data-page]").forEach(n => n.classList.toggle("active", n.dataset.page === name)); $("page-title").textContent = name === "overview" ? "Good to see you." : name === "practice" ? "Practice Arena" : name === "official" ? "Official rounds" : "Profile & resume"; }
async function loadDashboard() {
  const [me, catalog] = await Promise.all([api("/api/me"), api("/api/catalog")]); csrf = me.csrf_token; roles = catalog.roles; roleNames = catalog.role_names || []; companies = catalog.companies || [];
  $("auth-view").hidden = true; $("app-view").hidden = false; $("xp").textContent = me.xp; $("streak").textContent = `${me.streak.current_count} days`; $("profile-email").textContent = me.user.email; $("avatar").textContent = (me.profile.display_name || me.user.email)[0].toUpperCase();
  $("college").value = me.profile.college || ""; $("graduation-year").value = me.profile.graduation_year || "";
  $("preferred-role").value = me.profile.preferred_role || ""; $("preferred-company").value = me.profile.preferred_company || "";
  $("practice-role").innerHTML = roleNames.map(name => `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join("");
  $("practice-company").innerHTML = companies.map(company => `<option value="${escapeHTML(company.name)}">${escapeHTML(company.name)}</option>`).join("");
}
document.querySelectorAll("[data-auth]").forEach(b => b.onclick = () => {
  authMode = b.dataset.auth;
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === b));
  $("name-field").hidden = authMode !== "register";
  $("confirm-password-field").hidden = authMode !== "register";
  $("name").required = authMode === "register";
  $("confirm-password").required = authMode === "register";
  $("password").autocomplete = authMode === "register" ? "new-password" : "current-password";
  $("password-help").textContent = authMode === "register"
    ? "(8+, uppercase, lowercase, number, symbol)"
    : "(8+ characters)";
  $("auth-submit").innerHTML = authMode === "register" ? "Create account <span>→</span>" : "Log in <span>→</span>";
});
$("auth-form").onsubmit = async e => {
  e.preventDefault();
  $("auth-error").textContent = "";
  if (authMode === "register" && $("password").value !== $("confirm-password").value) {
    $("auth-error").textContent = "Passwords do not match.";
    return;
  }
  try {
    const d = await api(`/api/${authMode}`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({
        email: $("email").value,
        password: $("password").value,
        confirm_password: $("confirm-password").value,
        name: $("name").value
      })
    });
    csrf = d.csrf_token;
    await loadDashboard();
  } catch (x) {
    $("auth-error").textContent = x.message;
  }
};
document.querySelectorAll("[data-page], [data-page-jump]").forEach(b => b.onclick = () => showPage(b.dataset.page || b.dataset.pageJump));
$("logout").onclick = async () => { try { await api("/api/logout", {method:"POST"}); location.reload(); } catch (e) { flash(e.message, true); } };
$("practice-form").onsubmit = async e => { e.preventDefault(); const out = $("practice-output"); out.innerHTML = "<p>Generating thoughtful questions…</p>"; try { const d = await api("/api/assessment", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({role_name:$("practice-role").value, company_name:$("practice-company").value, mode:"practice"})}); out.innerHTML = `<h3>${escapeHTML(d.role.name)} · ${escapeHTML(d.role.company)}</h3><ol>${d.questions.map(q => typeof q === "string" ? `<li>${escapeHTML(q)}</li>` : `<li><strong>${escapeHTML(q.question)}</strong><small>${escapeHTML(q.category || "")}</small><ul>${q.options.map(option => `<li>${escapeHTML(option)}</li>`).join("")}</ul><p>${escapeHTML(q.explanation || "")}</p></li>`).join("")}</ol><p class="success">${d.questions.length} practice questions are ready.</p>`; } catch(x) { out.textContent=x.message; } };
$("resume-form").onsubmit = async e => { e.preventDefault(); const fd = new FormData(); fd.append("resume", $("resume").files[0]); try { const d=await api("/api/resume",{method:"POST",body:fd}); $("resume-result").innerHTML = `<p class="success">Resume saved (${d.text.length} characters extracted).</p>`; } catch(x) { flash(x.message,true); } };
document.querySelectorAll(".round-start").forEach(b => b.onclick = () => {
  window.location.href = `/round/${Number(b.dataset.round)}`;
});
$("profile-form").onsubmit = async e => { e.preventDefault(); try { await api("/api/profile",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({college:$("college").value,graduation_year:$("graduation-year").value,preferred_role:$("preferred-role").value,preferred_company:$("preferred-company").value})}); flash("Profile saved."); } catch(x) { flash(x.message,true); } };
if (window.interviewForgeLoggedIn) loadDashboard().catch(() => {});
