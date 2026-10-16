/* ==========================================================================
   BELAY DEMEKE FAMILY WEBSITE — front-end behaviour
   No dependencies. Everything degrades gracefully when JS is unavailable.
   ========================================================================== */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const csrfToken = () => {
    const tag = document.querySelector('meta[name="csrf-token"]');
    return tag ? tag.getAttribute("content") : "";
  };

  /* ---------------------------------------------------------------- header */
  const header = $("#siteHeader");
  const toTop = $("#toTop");

  const onScroll = () => {
    const y = window.scrollY;
    if (header) header.classList.toggle("is-scrolled", y > 12);
    if (toTop) toTop.classList.toggle("is-visible", y > 500);
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  if (toTop) {
    toTop.addEventListener("click", () =>
      window.scrollTo({ top: 0, behavior: "reduce" in document.documentElement.style ? "auto" : "smooth" })
    );
  }

  /* ---------------------------------------------------- light / dark theme */
  const THEME_KEY = "belay-theme";
  const themeBtn = $("#themeToggle");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");

  const readTheme = () =>
    document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";

  const paintThemeButton = () => {
    if (!themeBtn) return;
    const dark = readTheme() === "dark";
    themeBtn.setAttribute("aria-pressed", String(dark));
    themeBtn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
  };

  const applyTheme = (theme, remember) => {
    document.documentElement.setAttribute("data-theme", theme);
    paintThemeButton();
    if (!remember) return;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (e) {
      /* Private browsing: the theme still switches, it just is not kept. */
    }
  };

  paintThemeButton();

  if (themeBtn) {
    themeBtn.addEventListener("click", () =>
      applyTheme(readTheme() === "dark" ? "light" : "dark", true)
    );
  }

  // Follow the operating system until the visitor picks one themselves.
  const onSystemTheme = (event) => {
    let saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* no storage */ }
    if (!saved) applyTheme(event.matches ? "dark" : "light", false);
  };
  if (prefersDark.addEventListener) prefersDark.addEventListener("change", onSystemTheme);
  else if (prefersDark.addListener) prefersDark.addListener(onSystemTheme);

  /* ------------------------------------------------ section pill highlight
     Only the landing page owns these sections. The dashboard has its own
     #announcements and #events panels, which would light up a pill that is
     nowhere near the screen, so the strip just links back from other pages. */
  const onHome = document.body.classList.contains("home-page");
  const pills = $$(".main-nav a[href*='#']").filter((a) => a.hash.length > 1);
  const watched = onHome
    ? pills.map((a) => document.getElementById(a.hash.slice(1))).filter(Boolean)
    : [];

  if ("IntersectionObserver" in window && watched.length) {
    const highlight = (id) =>
      pills.forEach((a) => a.classList.toggle("is-active", a.hash.slice(1) === id));

    /* The watched strip is a thin slice near the top of the viewport, so two
       sections can touch it while the page scrolls between them. The one whose
       edge sits nearest that slice is the section being entered. */
    const nearest = (list) =>
      list.reduce((best, entry) =>
        Math.abs(entry.boundingClientRect.top - innerHeight * 0.4) <
        Math.abs(best.boundingClientRect.top - innerHeight * 0.4)
          ? entry
          : best);

    const spy = new IntersectionObserver(
      (entries) => {
        const onScreen = entries.filter((entry) => entry.isIntersecting);
        if (onScreen.length) highlight(nearest(onScreen).target.id);
      },
      { rootMargin: "-40% 0px -55% 0px" }
    );
    watched.forEach((section) => spy.observe(section));

    /* Light the clicked pill straight away: the smooth scroll takes a moment
       and the observer only catches up once it settles. */
    pills.forEach((a) => a.addEventListener("click", () => highlight(a.hash.slice(1))));

    const linked = pills.find((a) => a.hash === location.hash);
    highlight((linked || pills[0]).hash.slice(1));
  }

  /* ------------------------------------------------------ notification bell */
  const bell = $("#bell");
  const bellBtn = $("#bellBtn");
  const bellPanel = $("#bellPanel");

  const postJson = (url, body) =>
    fetch(url, {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken() },
      body: body || null,
    }).then((res) => res.ok).catch(() => false);

  const settleNotifications = () => {
    $$(".notif-row.is-unread").forEach((row) => row.classList.remove("is-unread"));
    $$(".notif-mark").forEach((form) => form.remove());
    $$("form[data-ajax]").forEach((form) => form.remove());
    $$(".tab-count").forEach((badge) => badge.remove());
    const badge = $("#bellBadge");
    if (badge) badge.remove();
    const count = $("#notifUnread");
    if (count) count.hidden = true;
  };

  if (bell && bellBtn && bellPanel) {
    const closeBell = () => {
      bellPanel.hidden = true;
      bellBtn.setAttribute("aria-expanded", "false");
    };

    bellBtn.addEventListener("click", () => {
      const open = bellPanel.hidden;
      bellPanel.hidden = !open;
      bellBtn.setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", (e) => {
      if (!bellPanel.hidden && !e.target.closest("#bell")) closeBell();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeBell();
    });

    // Opening a notification marks it read without a second page load.
    bellPanel.addEventListener("click", (e) => {
      const link = e.target.closest(".notif-body");
      if (!link) return;
      const row = link.closest(".notif-row");
      if (row && row.classList.contains("is-unread")) {
        row.classList.remove("is-unread");
        postJson(`/notifications/${encodeURIComponent(row.dataset.notifId)}/read`);
      }
    });
  }

  $$("form[data-ajax]").forEach((form) =>
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = new FormData(form);
      if (await postJson(form.getAttribute("action"), data)) settleNotifications();
    })
  );

  /* --------------------------------------------------------- flash messages */
  $$(".flash-close").forEach((btn) =>
    btn.addEventListener("click", () => btn.closest(".flash").remove())
  );
  $$(".flash").forEach((el) => setTimeout(() => el.remove(), 7000));

  /* -------------------------------------------------------- scroll reveal */
  const revealables = $$(".reveal");
  if ("IntersectionObserver" in window && revealables.length) {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry, i) => {
          if (!entry.isIntersecting) return;
          // Stagger siblings slightly for a smoother cascade.
          entry.target.style.transitionDelay = `${Math.min(i * 60, 240)}ms`;
          entry.target.classList.add("is-visible");
          io.unobserve(entry.target);
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
    revealables.forEach((el) => io.observe(el));
  } else {
    revealables.forEach((el) => el.classList.add("is-visible"));
  }

  /* ------------------------------------------------------ animated counters */
  const counters = $$("[data-count]");
  if (counters.length) {
    const animate = (el) => {
      const target = parseInt(el.dataset.count, 10);
      if (!Number.isFinite(target)) return;
      if (target === 0) { el.textContent = "0"; return; }
      const duration = 1100;
      const start = performance.now();
      const step = (now) => {
        const p = Math.min((now - start) / duration, 1);
        el.textContent = String(Math.round(target * (1 - Math.pow(1 - p, 3))));
        if (p < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    };

    if ("IntersectionObserver" in window) {
      const co = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          animate(entry.target);
          co.unobserve(entry.target);
        });
      }, { threshold: 0.5 });
      counters.forEach((el) => co.observe(el));
    } else {
      counters.forEach(animate);
    }
  }

  /* ---------------------------------------------------------------- lightbox */
  const lightbox = $("#lightbox");
  const lightboxBody = $("#lightboxBody");
  const lightboxClose = $("#lightboxClose");
  let lastFocused = null;

  const openLightbox = (src, caption, isVideo = false) => {
    if (!lightbox || !lightboxBody) return;
    lastFocused = document.activeElement;
    lightboxBody.innerHTML = "";

    const media = document.createElement(isVideo ? "video" : "img");
    media.src = src;
    if (isVideo) { media.controls = true; media.autoplay = true; media.playsInline = true; }
    else media.alt = caption || "Enlarged photo";
    lightboxBody.appendChild(media);

    if (caption) {
      const p = document.createElement("p");
      p.className = "lightbox-caption";
      p.textContent = caption;
      lightboxBody.appendChild(p);
    }

    lightbox.hidden = false;
    document.body.style.overflow = "hidden";
    lightboxClose && lightboxClose.focus();
  };

  const closeLightbox = () => {
    if (!lightbox || lightbox.hidden) return;
    lightbox.hidden = true;
    lightboxBody.innerHTML = "";
    document.body.style.overflow = "";
    if (lastFocused) lastFocused.focus();
  };

  if (lightbox) {
    lightboxClose && lightboxClose.addEventListener("click", closeLightbox);
    lightbox.addEventListener("click", (e) => { if (e.target === lightbox) closeLightbox(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLightbox(); });

    // Home page gallery tiles
    $$(".gallery-item").forEach((item) => {
      item.addEventListener("click", () => openLightbox(item.dataset.src, item.dataset.caption));
      item.setAttribute("tabindex", "0");
      item.setAttribute("role", "button");
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); item.click(); }
      });
    });

    // Album thumbnails (event delegation — survives re-renders)
    document.addEventListener("click", (e) => {
      const img = e.target.closest("[data-lightbox]");
      if (img) openLightbox(img.dataset.lightbox, img.dataset.caption);
    });
  }

  /* ------------------------------------------------------- password toggles */
  $$(".toggle-pass").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.target);
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.textContent = show ? "Hide" : "Show";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
  });

  /* ------------------------------------------------- register: strength meter */
  const strengthFill = $("#strengthFill");
  const strengthLabel = $("#strengthLabel");
  const newPassword = $("#password");
  const confirmPassword = $("#confirm_password");
  const matchHint = $("#matchHint");

  if (strengthFill && newPassword) {
    const levels = [
      { min: 0, label: "Too short", color: "var(--danger)", width: "8%" },
      { min: 8, label: "Weak", color: "var(--danger)", width: "30%" },
      { min: 10, label: "Fair", color: "var(--gold-600)", width: "55%" },
      { min: 12, label: "Good", color: "var(--info)", width: "78%" },
      { min: 14, label: "Strong", color: "var(--success)", width: "100%" },
    ];

    const score = () => {
      const value = newPassword.value;
      let level = levels[0];
      for (const l of levels) if (value.length >= l.min) level = l;
      // Require mixed character classes before crediting a high score.
      const variety = [/[A-Za-z]/, /\d/, /[^\w\s]/].filter((r) => r.test(value)).length;
      if (value.length >= 8 && variety < 2) level = levels[1];
      return level;
    };

    const paint = () => {
      const level = newPassword.value ? score() : levels[0];
      strengthFill.style.width = newPassword.value ? level.width : "0";
      strengthFill.style.background = level.color;
      strengthLabel.textContent = newPassword.value ? `Strength: ${level.label}` : "Password strength";
    };

    const checkMatch = () => {
      if (!matchHint || !confirmPassword) return;
      if (!confirmPassword.value) { matchHint.textContent = ""; return; }
      const ok = confirmPassword.value === newPassword.value;
      matchHint.textContent = ok ? "Passwords match." : "Passwords do not match.";
      matchHint.style.color = ok ? "var(--success)" : "var(--danger)";
    };

    newPassword.addEventListener("input", () => { paint(); checkMatch(); });
    confirmPassword && confirmPassword.addEventListener("input", checkMatch);
    paint();
  }

  /* ------------------------------------------------ confirm-before-submit forms */
  $$("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  /* ============================================================
     DASHBOARD
     ============================================================ */
  const tabs = $$("#dashTabs .tab");

  if (tabs.length) {
    const panels = $$(".panel");

    const activate = (name, updateHash = true) => {
      const tab = tabs.find((t) => t.dataset.tab === name) || tabs[0];
      tabs.forEach((t) => {
        const on = t === tab;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", String(on));
      });
      panels.forEach((p) => p.classList.toggle("is-active", p.id === tab.dataset.tab));
      if (updateHash) history.replaceState(null, "", `#${tab.dataset.tab}`);
      tab.scrollIntoView({ block: "nearest", inline: "nearest" });
    };

    tabs.forEach((tab) => tab.addEventListener("click", () => activate(tab.dataset.tab)));

    // Jump links such as "Share a Memory" inside empty states.
    $$("[data-goto]").forEach((el) => el.addEventListener("click", () => activate(el.dataset.goto)));

    // Deep links from redirects like /dashboard#profile
    const fromHash = (location.hash || "").replace("#", "");
    activate(fromHash && tabs.some((t) => t.dataset.tab === fromHash) ? fromHash : "overview", false);
  }

  /* ---------------------------------------------------------- album filters */
  const filterWrap = $("#albumFilters");
  if (filterWrap) {
    const cards = $$("#mediaFeed .media-card");
    const feedStatus = $("#feedStatus");

    filterWrap.addEventListener("click", (e) => {
      const btn = e.target.closest(".chip-btn");
      if (!btn) return;
      $$(".chip-btn", filterWrap).forEach((b) => b.classList.toggle("is-active", b === btn));

      const filter = btn.dataset.filter;
      let visible = 0;
      cards.forEach((card) => {
        const show = filter === "all" || card.dataset.kind === filter;
        card.style.display = show ? "" : "none";
        if (show) visible++;
      });
      if (feedStatus) feedStatus.hidden = visible > 0;
    });
  }

  /* ---------------------------------------------------------------- upload */
  const uploadForm = $("#uploadForm");

  if (uploadForm) {
    const dropZone = $("#dropZone");
    const fileInput = $("#fileInput");
    const preview = $("#preview");
    const submitBtn = $("#submitBtn");
    const status = $("#uploadStatus");
    const progress = $("#uploadProgress");

    const IMAGE_EXT = ["png", "jpg", "jpeg", "gif", "webp"];
    const VIDEO_EXT = ["mp4", "mov", "webm", "m4v"];
    const DOC_EXT = ["pdf", "docx", "xlsx", "txt"];
    const ALLOWED = [...IMAGE_EXT, ...VIDEO_EXT, ...DOC_EXT];
    const MAX_BYTES = 100 * 1024 * 1024;

    let selected = [];

    const extOf = (name) => (name.includes(".") ? name.split(".").pop().toLowerCase() : "");
    const kindOf = (name) => {
      const ext = extOf(name);
      if (IMAGE_EXT.includes(ext)) return "image";
      if (VIDEO_EXT.includes(ext)) return "video";
      return "document";
    };
    const sizeOf = (bytes) =>
      bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

    const setStatus = (text, kind = "") => {
      if (!status) return;
      status.textContent = text;
      status.className = `form-status ${kind}`.trim();
    };

    const renderPreviews = () => {
      if (!preview) return;
      preview.innerHTML = "";

      selected.forEach((file, index) => {
        const item = document.createElement("div");
        item.className = "preview-item";

        const kind = kindOf(file.name);
        const url = URL.createObjectURL(file);

        if (kind === "image") {
          const img = document.createElement("img");
          img.src = url;
          img.alt = file.name;
          img.addEventListener("load", () => URL.revokeObjectURL(url));
          item.appendChild(img);
        } else if (kind === "video") {
          const video = document.createElement("video");
          video.src = url;
          video.muted = true;
          video.playsInline = true;
          item.appendChild(video);
        } else {
          const box = document.createElement("div");
          box.className = "preview-file";
          box.innerHTML = `<span style="font-size:1.6rem">&#128196;</span><span>${file.name}</span><span>${sizeOf(file.size)}</span>`;
          item.appendChild(box);
        }

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "preview-remove";
        remove.innerHTML = "&times;";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.addEventListener("click", () => {
          selected.splice(index, 1);
          renderPreviews();
        });
        item.appendChild(remove);

        preview.appendChild(item);
      });
    };

    const addFiles = (list) => {
      const rejected = [];
      Array.from(list).forEach((file) => {
        if (!ALLOWED.includes(extOf(file.name))) { rejected.push(`${file.name} (unsupported type)`); return; }
        if (file.size > MAX_BYTES) { rejected.push(`${file.name} (over 100 MB)`); return; }
        if (selected.some((f) => f.name === file.name && f.size === file.size)) return;
        selected.push(file);
      });
      renderPreviews();
      if (rejected.length) setStatus(`Skipped: ${rejected.join(", ")}`, "err");
      else if (selected.length) setStatus(`${selected.length} file${selected.length > 1 ? "s" : ""} ready to upload.`);
    };

    // Post Image / Post Video / Post File buttons
    const pickers = [
      ["#postImageBtn", "image/*"],
      ["#postVideoBtn", "video/*"],
      ["#postFileBtn", ".pdf,.docx,.xlsx,.txt"],
    ];
    pickers.forEach(([sel, accept]) => {
      const btn = $(sel);
      if (!btn) return;
      btn.addEventListener("click", () => {
        fileInput.accept = accept;
        fileInput.value = "";
        fileInput.click();
      });
    });

    if (dropZone && fileInput) {
      dropZone.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        fileInput.accept = "image/*,video/*,.pdf,.docx,.xlsx,.txt";
        fileInput.value = "";
        fileInput.click();
      });
      dropZone.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); dropZone.click(); }
      });

      ["dragenter", "dragover"].forEach((evt) =>
        dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.add("is-over"); })
      );
      ["dragleave", "drop"].forEach((evt) =>
        dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.remove("is-over"); })
      );
      dropZone.addEventListener("drop", (e) => {
        if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
      });
    }

    fileInput && fileInput.addEventListener("change", () => {
      addFiles(fileInput.files);
      fileInput.value = "";
    });

    uploadForm.addEventListener("submit", (e) => {
      e.preventDefault();

      const title = $("#title", uploadForm).value.trim();
      if (!title) { setStatus("Please give your post a title.", "err"); $("#title", uploadForm).focus(); return; }
      if (!selected.length) { setStatus("Please choose at least one photo or video.", "err"); return; }

      const data = new FormData();
      data.append("csrf_token", csrfToken());
      data.append("title", title);
      data.append("desc", $("#desc", uploadForm).value.trim());
      data.append("visibility", $("#visibility", uploadForm).value);
      selected.forEach((file) => data.append("files", file));

      submitBtn.disabled = true;
      if (progress) progress.hidden = false;
      setStatus("Uploading...");

      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/posts");

      xhr.upload.addEventListener("progress", (ev) => {
        if (!ev.lengthComputable || !progress) return;
        progress.firstElementChild.style.animation = "none";
        progress.firstElementChild.style.width = `${Math.round((ev.loaded / ev.total) * 100)}%`;
      });

      xhr.addEventListener("load", () => {
        submitBtn.disabled = false;
        if (progress) { progress.hidden = true; progress.firstElementChild.style.width = "35%"; progress.firstElementChild.style.animation = ""; }

        let body = {};
        try { body = JSON.parse(xhr.responseText); } catch (_) { /* non-JSON error page */ }

        if (xhr.status >= 200 && xhr.status < 300 && body.success) {
          setStatus(`Shared ${body.count} item${body.count > 1 ? "s" : ""} with the family.`, "ok");
          setTimeout(() => window.location.reload(), 900);
          return;
        }

        setStatus(body.error || `Upload failed (HTTP ${xhr.status}). Please try again.`, "err");
      });

      xhr.addEventListener("error", () => {
        submitBtn.disabled = false;
        if (progress) progress.hidden = true;
        setStatus("Network error. Please check your connection and try again.", "err");
      });

      xhr.send(data);
    });
  }

  /* --------------------------------------------------------- delete a post */
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".delete-btn");
    if (!btn) return;
    if (!window.confirm("Delete this memory for everyone? This cannot be undone.")) return;

    btn.disabled = true;
    try {
      const res = await fetch(`/posts/${encodeURIComponent(btn.dataset.id)}/delete`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
      });
      const body = await res.json().catch(() => ({}));

      if (res.ok && body.success) {
        const card = btn.closest(".media-card");
        if (card) {
          card.style.transition = "opacity .3s, transform .3s";
          card.style.opacity = "0";
          card.style.transform = "scale(.96)";
          setTimeout(() => card.remove(), 300);
        }
      } else {
        window.alert(body.error || "Could not delete that memory.");
        btn.disabled = false;
      }
    } catch (_) {
      window.alert("Network error. Please try again.");
      btn.disabled = false;
    }
  });

  /* ---------------------------------------------------------- edit a memory */
  const editModal = $("#editModal");

  if (editModal) {
    const editForm = $("#editForm");
    const titleInput = $("#editTitle");
    const descInput = $("#editDesc");
    const visSelect = $("#editVisibility");
    const saveBtn = $("#editSave");
    const status = $("#editStatus");
    let activeCard = null;
    let postId = null;

    const setStatus = (text, kind = "") => {
      status.textContent = text;
      status.className = `form-status ${kind}`.trim();
    };

    const openModal = (btn) => {
      postId = btn.dataset.id;
      activeCard = btn.closest(".media-card");
      titleInput.value = btn.dataset.title || "";
      descInput.value = btn.dataset.desc || "";
      visSelect.value = btn.dataset.visibility || "family";
      setStatus("");
      editModal.hidden = false;
      document.body.style.overflow = "hidden";
      titleInput.focus();
    };

    const closeModal = () => {
      editModal.hidden = true;
      document.body.style.overflow = "";
      saveBtn.disabled = false;
      postId = null;
      activeCard = null;
    };

    document.addEventListener("click", (e) => {
      const btn = e.target.closest(".edit-btn");
      if (btn) openModal(btn);
    });

    $("#editModalClose").addEventListener("click", closeModal);
    $("#editCancel").addEventListener("click", closeModal);
    editModal.addEventListener("click", (e) => { if (e.target === editModal) closeModal(); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !editModal.hidden) closeModal();
    });

    const paintCard = (card, data) => {
      card.querySelector("h3").textContent = data.title;
      const zoomable = card.querySelector("[data-lightbox]");
      if (zoomable) zoomable.dataset.caption = data.title;

      const body = card.querySelector(".media-body");
      let desc = body.querySelector("p");
      if (data.desc) {
        if (!desc) {
          desc = document.createElement("p");
          body.insertBefore(desc, body.querySelector(".media-meta"));
        }
        desc.textContent = data.desc;
      } else if (desc) {
        desc.remove();
      }

      const preview = card.querySelector(".media-preview");
      let pub = preview.querySelector(".chip-public");
      if (data.visibility === "public" && !pub) {
        pub = document.createElement("span");
        pub.className = "chip chip-public";
        pub.textContent = "Public";
        preview.appendChild(pub);
      } else if (data.visibility !== "public" && pub) {
        pub.remove();
      }

      const editBtn = card.querySelector(".edit-btn");
      if (editBtn) {
        editBtn.dataset.title = data.title;
        editBtn.dataset.desc = data.desc || "";
        editBtn.dataset.visibility = data.visibility;
      }
    };

    editForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = titleInput.value.trim();
      if (!title) { setStatus("Please give the memory a title.", "err"); titleInput.focus(); return; }

      saveBtn.disabled = true;
      setStatus("Saving...");
      try {
        const res = await fetch(`/posts/${encodeURIComponent(postId)}/edit`, {
          method: "POST",
          headers: { "X-CSRF-Token": csrfToken() },
          body: new URLSearchParams({
            title,
            desc: descInput.value.trim(),
            visibility: visSelect.value,
          }),
        });
        const body = await res.json().catch(() => ({}));

        if (res.ok && body.success) {
          if (activeCard) paintCard(activeCard, body);
          setStatus("Saved.", "ok");
          setTimeout(closeModal, 600);
        } else {
          setStatus(body.error || "Could not save that memory. Please try again.", "err");
          saveBtn.disabled = false;
        }
      } catch (_) {
        setStatus("Network error. Please try again.", "err");
        saveBtn.disabled = false;
      }
    });
  }
})();
