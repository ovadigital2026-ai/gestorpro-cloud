/**
 * GestorPro Cloud Bridge — un solo URL, usuarios compartidos en el servidor
 */
(function () {
  if (!window.__GESTORPRO_CLOUD__) return;

  const API = (window.__GESTORPRO_API__ || "").replace(/\/$/, "");

  function apiUrl(path) {
    return API + path;
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    const token = localStorage.getItem("gestorpro_cloud_token");
    if (token) headers["X-Session-Token"] = token;
    const res = await fetch(apiUrl(path), {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    let data = {};
    try {
      data = await res.json();
    } catch (e) {}
    if (!res.ok) {
      const err = new Error((data && data.error) || "Error " + res.status);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  DB._cloudCache = null;
  DB._cloudUsersCache = [];
  DB._cloudDefaults = [];
  DB._saveTimer = null;

  DB.getSession = function () {
    try {
      const raw = localStorage.getItem("gestorpro_cloud_session");
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  };

  DB.setSession = function (user, impersonating) {
    // Compatible con App.impersonateUser(setSession(adminUser, userId))
    // y login setSession(user, token) cuando token es string largo
    const prev = DB.getSession() || {};
    let token = localStorage.getItem("gestorpro_cloud_token") || prev.token;
    let imp = prev.impersonating || null;

    if (typeof impersonating === "string") {
      if (impersonating.length > 40 || impersonating.indexOf("-") !== -1 && impersonating.length > 20) {
        // parece token
        token = impersonating;
        imp = null;
      } else if (impersonating === "" || impersonating === null) {
        imp = null;
      } else {
        // userId a impersonar
        imp = impersonating;
      }
    } else if (impersonating === null || impersonating === undefined) {
      // keep or clear
      if (arguments.length >= 2) imp = null;
    }

    const session = {
      userId: user.id || user.userId,
      username: user.username,
      name: user.name || user.username,
      role: user.role || "user",
      token: token,
      loginAt: new Date().toISOString(),
    };
    if (imp) session.impersonating = imp;
    localStorage.setItem("gestorpro_cloud_session", JSON.stringify(session));
    if (token) localStorage.setItem("gestorpro_cloud_token", token);
  };

  DB.clearSession = function () {
    const token = localStorage.getItem("gestorpro_cloud_token");
    localStorage.removeItem("gestorpro_cloud_session");
    localStorage.removeItem("gestorpro_cloud_token");
    DB._cloudCache = null;
    if (token) api("/api/logout", { method: "POST" }).catch(function () {});
  };

  DB.currentUserId = function () {
    const s = DB.getSession();
    if (!s) return null;
    return s.impersonating || s.userId;
  };

  DB.isAdmin = function () {
    const s = DB.getSession();
    return !!(s && s.role === "admin" && !s.impersonating);
  };

  DB._cloudPull = async function () {
    const session = DB.getSession();
    if (!session) return null;
    let path = "/api/data";
    if (session.impersonating) path += "?userId=" + encodeURIComponent(session.impersonating);
    const data = await api(path);
    DB._cloudCache = data.data;
    return DB._cloudCache;
  };

  DB._cloudPush = async function () {
    if (!DB._cloudCache) return;
    const session = DB.getSession();
    if (!session) return;
    let path = "/api/data";
    if (session.impersonating) path += "?userId=" + encodeURIComponent(session.impersonating);
    await api(path, { method: "PUT", body: DB._cloudCache });
  };

  DB._cloudScheduleSave = function () {
    clearTimeout(DB._saveTimer);
    DB._saveTimer = setTimeout(function () {
      DB._cloudPush().catch(function (e) {
        console.error(e);
        if (window.Utils) Utils.toast("Error al guardar en la nube", "error");
      });
    }, 300);
  };

  DB.getData = function () {
    if (!DB._cloudCache) {
      DB._cloudCache = {
        clients: [],
        services: [],
        contracts: [],
        settings: { currency: "USD", currencySymbol: "$", businessName: "Mi Negocio", alertDays: 5 },
        meta: {},
      };
    }
    return DB._cloudCache;
  };

  DB.saveData = function (data) {
    DB._cloudCache = data;
    DB._cloudScheduleSave();
  };

  DB.getUsers = function () {
    return DB._cloudUsersCache || [];
  };

  DB.getUserById = function (id) {
    return (DB._cloudUsersCache || []).find(function (u) {
      return u.id === id;
    });
  };

  DB.getUserByUsername = function (username) {
    const u = (username || "").toLowerCase();
    return (DB._cloudUsersCache || []).find(function (x) {
      return (x.username || "").toLowerCase() === u;
    });
  };

  DB._cloudLoadUsers = async function () {
    const data = await api("/api/users");
    DB._cloudUsersCache = data.users || [];
    return DB._cloudUsersCache;
  };

  DB._cloudLogin = async function (username, password) {
    if (!username || !password) {
      throw new Error("Completá usuario y contraseña");
    }
    let data;
    try {
      data = await api("/api/login", {
        method: "POST",
        body: { username: username, password: password },
      });
    } catch (e) {
      if (e.status === 401) throw new Error("Usuario o contraseña incorrectos");
      if (e.status === 403) throw new Error("Usuario desactivado");
      throw new Error(e.message || "Error de conexión con el servidor");
    }
    if (!data || !data.token) throw new Error("Respuesta inválida del servidor");
    localStorage.setItem("gestorpro_cloud_token", data.token);
    DB.setSession(data.user, data.token);
    try {
      await DB._cloudPull();
    } catch (e) {
      console.warn("pull after login", e);
    }
    if (data.user.role === "admin") {
      try {
        await DB._cloudLoadUsers();
        const defs = await api("/api/defaults");
        DB._cloudDefaults = defs.defaults || [];
      } catch (e) {}
    }
    return { user: data.user };
  };

  DB.createUser = function (opts) {
    // sync stub — App must use async path; we monkey-patch form
    return { _cloudNeedAsync: true, opts: opts };
  };

  DB.updateUser = function (id, updates) {
    return { _cloudNeedAsync: true, id: id, updates: updates };
  };

  DB.deleteUser = function (id) {
    return { _cloudNeedAsync: true, id: id };
  };

  DB.resetUserData = function (id) {
    return { _cloudNeedAsync: true, id: id };
  };

  DB.getDefaultServices = function () {
    return DB._cloudDefaults && DB._cloudDefaults.length
      ? DB._cloudDefaults
      : DB.DEFAULT_SERVICES || [];
  };

  DB.setDefaultServices = function (services) {
    DB._cloudDefaults = services;
    api("/api/defaults", { method: "PUT", body: services }).catch(console.error);
  };

  function patchApp() {
    if (!window.App || window.__GESTORPRO_CLOUD_PATCHED__) return !!window.App;

    // Login (siempre re-enlaza el form cloud)
    const _showLogin = App.showLogin.bind(App);
    App.showLogin = function () {
      _showLogin();
      const form = document.getElementById("login-form");
      if (!form) return;

      // Toggle ver contraseña (por si el bridge corre después)
      const passInput = document.getElementById("login-pass");
      const toggleBtn = document.getElementById("toggle-pass");
      if (toggleBtn && passInput && !toggleBtn._bound) {
        toggleBtn._bound = true;
        toggleBtn.onclick = function () {
          const show = passInput.type === "password";
          passInput.type = show ? "text" : "password";
          toggleBtn.textContent = show ? "🙈" : "👁️";
        };
      }

      form.onsubmit = async function (e) {
        e.preventDefault();
        const errEl = document.getElementById("login-error");
        const btn = document.getElementById("login-submit");
        const username = (document.getElementById("login-user") || {}).value || "";
        const password = (document.getElementById("login-pass") || {}).value || "";
        try {
          if (errEl) {
            errEl.style.display = "none";
            errEl.textContent = "";
          }
          if (btn) {
            btn.disabled = true;
            btn.textContent = "Entrando…";
          }
          await DB._cloudLogin(username.trim(), password);
          await App.showApp();
        } catch (err) {
          console.error("login", err);
          const msg =
            (err && err.message) ||
            "No se pudo iniciar sesión. Revisá usuario/contraseña o esperá 1 min (servidor free).";
          if (errEl) {
            errEl.style.display = "block";
            errEl.textContent = msg;
          } else {
            alert(msg);
          }
        } finally {
          if (btn) {
            btn.disabled = false;
            btn.textContent = "Entrar";
          }
        }
      };
    };

    const _showApp = App.showApp.bind(App);
    App.showApp = async function () {
      try {
        if (DB.getSession()) {
          await DB._cloudPull();
          if (DB.getSession().role === "admin") {
            try {
              await DB._cloudLoadUsers();
              const defs = await api("/api/defaults");
              DB._cloudDefaults = defs.defaults || [];
            } catch (e) {}
          }
        }
      } catch (e) {
        if (e.status === 401) {
          DB.clearSession();
          App.showLogin();
          return;
        }
      }
      try { if (window.Notify && Notify.unlockAudio) Notify.unlockAudio(); } catch (e) {}
      try {
        document.body.addEventListener('click', function () {
          try { if (window.Notify && Notify.unlockAudio) Notify.unlockAudio(); } catch (e2) {}
        }, { once: false, passive: true });
      } catch (e) {}
      return _showApp();
    };

    // openUserModal: manejado en App.openUserModal (cloud + local)

    App.deleteUser = async function (id) {
      const user = DB.getUserById(id);
      if (!user) return;
      const ok = await Utils.confirm(
        '¿Eliminar al usuario "' + user.username + '"?',
        "Eliminar usuario"
      );
      if (!ok) return;
      try {
        await api("/api/users/" + encodeURIComponent(id), { method: "DELETE" });
        await DB._cloudLoadUsers();
        Utils.toast("Usuario eliminado", "success");
        App.refresh();
      } catch (e) {
        Utils.toast(e.message, "error");
      }
    };

    App.resetUserData = async function (id) {
      const ok = await Utils.confirm("¿Resetear todos los datos de este usuario?", "Reset");
      if (!ok) return;
      try {
        await api("/api/users/" + encodeURIComponent(id) + "/reset", { method: "POST" });
        Utils.toast("Datos reseteados", "success");
        App.refresh();
      } catch (e) {
        Utils.toast(e.message, "error");
      }
    };

    App.impersonateUser = async function (userId) {
      const session = DB.getSession();
      if (!session || session.role !== "admin") return;
      const user = DB.getUserById(userId);
      if (!user) return;
      session.impersonating = userId;
      localStorage.setItem("gestorpro_cloud_session", JSON.stringify(session));
      try {
        await DB._cloudPull();
        Utils.toast("Viendo datos de " + user.username, "info");
        App.navigate("dashboard");
      } catch (e) {
        Utils.toast(e.message, "error");
      }
    };

    App.stopImpersonate = async function () {
      const session = DB.getSession();
      if (!session) return;
      delete session.impersonating;
      localStorage.setItem("gestorpro_cloud_session", JSON.stringify(session));
      try {
        await DB._cloudPull();
        Utils.toast("Volviste al panel admin", "success");
        App.navigate("admin");
      } catch (e) {
        Utils.toast(e.message, "error");
      }
    };

    App.openDistributeModal = function () {
      const url = location.href.split("#")[0].split("?")[0];
      const users = (DB.getUsers() || []).filter(function (u) {
        return u.username !== "admin";
      });
      const opts = users
        .map(function (u) {
          return (
            '<option value="' +
            u.id +
            '" data-username="' +
            u.username +
            '" data-name="' +
            (u.name || u.username).replace(/"/g, "") +
            '">' +
            (u.name || u.username) +
            " (@" +
            u.username +
            ")</option>"
          );
        })
        .join("");
      Components.openModal(
        '<div class="modal-box p-5 max-w-lg">' +
          '<div class="flex items-center justify-between mb-3">' +
          '<h3 class="text-lg font-semibold">Compartir acceso</h3>' +
          '<button type="button" class="btn-icon btn-ghost" onclick="Components.closeModal()"><i data-lucide="x" class="w-5 h-5"></i></button></div>' +
          '<p class="text-sm text-slate-500 mb-3">Mensaje listo con <strong>link</strong>, usuario y saludo de <strong>OvaDigital</strong>.</p>' +
          '<label class="text-xs font-medium text-slate-500">Usuario</label>' +
          '<select id="share-user-select" class="input w-full mb-2">' +
          '<option value="">— Elegir usuario —</option>' +
          opts +
          "</select>" +
          '<label class="text-xs font-medium text-slate-500">Contraseña (la que le diste)</label>' +
          '<input id="share-user-pass" type="text" class="input w-full mb-2" placeholder="Contraseña del usuario" />' +
          '<label class="text-xs font-medium text-slate-500">Nombre del negocio en el saludo</label>' +
          '<input id="share-brand" type="text" class="input w-full mb-3" value="OvaDigital" />' +
          '<button type="button" class="btn btn-primary w-full" id="btn-share-access-go">Generar mensaje para WhatsApp</button>' +
          '<button type="button" class="btn btn-secondary w-full mt-2" id="btn-copy-only-url">Solo copiar link</button>' +
          '<button type="button" class="btn btn-ghost w-full mt-2" onclick="Components.closeModal()">Cerrar</button></div>'
      );
      if (window.lucide) lucide.createIcons();
      document.getElementById("btn-copy-only-url")?.addEventListener("click", function () {
        if (navigator.clipboard) {
          navigator.clipboard.writeText(url).then(function () {
            Utils.toast("Link copiado", "success");
          });
        } else prompt("Link:", url);
      });
      document.getElementById("btn-share-access-go")?.addEventListener("click", function () {
        const sel = document.getElementById("share-user-select");
        const pass = (document.getElementById("share-user-pass") || {}).value || "";
        const brand = (document.getElementById("share-brand") || {}).value || "OvaDigital";
        if (!sel || !sel.value) {
          Utils.toast("Elegí un usuario", "warning");
          return;
        }
        if (!pass) {
          Utils.toast("Escribí la contraseña que le asignaste", "warning");
          return;
        }
        const opt = sel.options[sel.selectedIndex];
        const username = opt.getAttribute("data-username");
        const name = opt.getAttribute("data-name");
        const text =
          "¡Hola! 👋\n\n" +
          "Gracias por contratar los servicios de " +
          brand +
          ".\n\n" +
          "Tu acceso a GestorPro:\n" +
          "🔗 " +
          url +
          "\n" +
          "👤 Usuario: " +
          username +
          "\n" +
          "🔑 Contraseña: " +
          pass +
          "\n\n" +
          "Abrí el link en Chrome e ingresá con tus datos.\n" +
          "¡Cualquier consulta, escribime!";
        Components.closeModal();
        if (App.showShareTextModal) {
          App.showShareTextModal(text, username);
        } else if (App.shareAccessMessage) {
          App.shareAccessMessage({ username: username, password: pass, name: name });
        }
      });
    };

    // Replace admin page button text sense via toast on first admin visit
    window.__GESTORPRO_CLOUD_PATCHED__ = true;
    console.log("[GestorPro] Modo NUBE activo");
    return true;
  }

  function boot() {
    patchApp();
    // Re-aplicar login cloud (App.init pudo haber pintado el form local antes)
    try {
      if (typeof App !== "undefined" && App.showLogin) {
        const hasSession = !!(DB.getSession && DB.getSession());
        if (!hasSession) App.showLogin();
      }
    } catch (e) {
      console.warn(e);
    }
    setTimeout(patchApp, 50);
    setTimeout(function () {
      patchApp();
      try {
        if (!DB.getSession()) App.showLogin();
      } catch (e) {}
    }, 300);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
