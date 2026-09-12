/* 视频管理器 前端逻辑（轻量原生 JS） */
const app = {
  state: { page: 1, total: 0, size: 30, loading: false, _reqToken: 0, categories: [], years: [], _ids: [], _tagdict: [], _reviews: [], _revIndex: -1, _revDone: null, _tags: [] },

  async api(url, opts) {
    const r = await fetch(url, Object.assign({}, opts));
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    return r.json();
  },

  qs() {
    const p = new URLSearchParams();
    if (document.getElementById("q").value.trim()) p.set("q", document.getElementById("q").value.trim());
    const cat = document.getElementById("fCat").value; if (cat) p.set("category", cat);
    const st = document.getElementById("fStatus").value; if (st) p.set("status", st);
    const yr = document.getElementById("fYear").value; if (yr) { p.set("year_from", yr); p.set("year_to", yr); }
    const mo = document.getElementById("fMonth").value; if (mo) p.set("month", mo);
    const selTags = this.state._tags || [];
    if (selTags.length) p.set("tags", selTags.join(","));   // 多选标签（OR）
    const stu = document.getElementById("fStudio"); if (stu && stu.value.trim()) p.set("studio", stu.value.trim());
    const mn = document.getElementById("fMin").value; if (mn) p.set("rating_min", mn);
    const fav = document.getElementById("fFav").checked; if (fav) p.set("favorite", 1);
    p.set("sort", document.getElementById("fSort").value);
    p.set("page", this.state.page); p.set("size", this.state.size);
    return p.toString();
  },

  // ---- 通用标签气泡（多选；6~8 栏、每栏最多 12 行）----
  // 通过 state._picker 区分上下文，复用同一套气泡 UI：
  //   kind="filter" 页面筛选栏（默认） / "bulk" 作品管理批量打标签 / "edit" 作品编辑标签
  // 每个上下文自带 anchor（定位锚点）、selected（初始勾选）、title/hint、onApply（应用回调）。
  toggleTagPanel(ev) {
    ev && ev.stopPropagation();
    this.openTagPicker(ev, {
      kind: "filter",
      anchor: document.getElementById("fTagBtn"),
      selected: this.state._tags || [],
      title: "标签筛选",
      hint: "满足任一标签即显示（OR）",
      onApply: (names) => {
        this.state._tags = names;
        this.renderSelectedTags();
        this.state.page = 1;
        this.load();
      },
    });
  },

  openTagPicker(ev, opts) {
    ev && ev.stopPropagation();
    opts = opts || {};
    const p = document.getElementById("fTagPanel");
    if (!p) return;
    const anchor = opts.anchor || null;
    // 再次点击同一锚点 → 收起（toggle）
    if (p.classList.contains("open") && this.state._picker
        && this.state._picker.kind === (opts.kind || "filter")
        && this.state._picker.anchor === anchor) {
      this.closeTagPanel();
      return;
    }
    this.state._picker = {
      kind: opts.kind || "filter",
      anchor: anchor,
      selected: new Set((opts.selected || []).filter(Boolean)),
      title: opts.title || "选择标签",
      hint: opts.hint || "多选",
      onApply: typeof opts.onApply === "function" ? opts.onApply : null,
    };
    p.classList.add("open");
    this.renderTagPickerHead();
    this.positionTagPanel(anchor);
    this.syncTagChecks();
    this.ensureTagColsLoaded();   // 列内容若尚未渲染（先开白屏管理），异步补齐
  },

  async ensureTagColsLoaded() {
    const box = document.getElementById("fTagCols");
    if (box && box.children.length) return;
    try {
      const t = await this.api("/api/tags");
      this.renderTagCols(t.items || []);
      this.syncTagChecks();
    } catch { /* 忽略：稍后可再打开 */ }
  },

  renderTagPickerHead() {
    const pk = this.state._picker || {};
    const title = document.getElementById("fTagTitle");
    if (title) title.textContent = pk.title || "选择标签";
    const hint = document.getElementById("fTagHint");
    if (hint) hint.textContent = pk.hint || "多选";
  },

  // 气泡用 fixed 定位并由 JS 计算坐标：始终贴在锚点下方、且渲染在最顶层
  positionTagPanel(anchor) {
    const btn = anchor || (this.state._picker && this.state._picker.anchor)
      || document.getElementById("fTagBtn");
    const p = document.getElementById("fTagPanel");
    if (!btn || !p || !p.classList.contains("open")) return;
    const r = btn.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    const w = Math.min(900, vw - 24);
    const left = Math.max(12, Math.min(r.left, vw - w - 12));
    const estH = Math.min(vh * 0.8, 420);          // 与 CSS max-height 一致
    let top = r.bottom + 8;
    if (top + estH > vh - 8 && r.top - 8 - estH > 0) top = r.top - estH - 8;  // 下方放不下则向上展开
    p.style.width = w + "px";
    p.style.left = left + "px";
    p.style.top = Math.max(8, top) + "px";
  },
  closeTagPanel() {
    const p = document.getElementById("fTagPanel");
    if (p) p.classList.remove("open");
    this.state._picker = null;
  },
  renderTagCols(tags) {
    const box = document.getElementById("fTagCols");
    if (!box) return;
    const list = tags || [];
    if (!list.length) { box.innerHTML = '<div class="muted" style="padding:8px">暂无可用标签</div>'; return; }
    const PER_COL = 12;                                   // 每栏最多 12 行
    const need = Math.ceil(list.length / PER_COL);
    // 栏数：至少 6 栏，最多 8 栏
    const cols = Math.max(6, Math.min(8, need));
    const buckets = Array.from({ length: cols }, () => []);
    list.forEach((t, i) => buckets[i % cols].push(t));    // 轮转分配，保持各栏均衡
    box.style.gridTemplateColumns = `repeat(${cols}, minmax(0,1fr))`;
    box.innerHTML = buckets.map((items, ci) => `
      <div class="tag-col">
        ${items.map(t => `
          <label class="tag-opt">
            <input type="checkbox" name="fTagOpt" value="${esc(t.name)}" onchange="app.onTagOptChange()">
            <span class="tag-opt-name">${esc(t.name)}</span>
            <span class="tag-opt-c">${t.c}</span>
          </label>`).join("")}
      </div>`).join("");
  },
  _pickerSelected() {
    if (this.state._picker) return this.state._picker.selected;
    return new Set(this.state._tags || []);
  },
  syncTagChecks() {
    const sel = this._pickerSelected();
    document.querySelectorAll('#fTagCols input[name="fTagOpt"]').forEach(cb => {
      cb.checked = sel.has(cb.value);
    });
    this.updateTagHint();
  },
  onTagOptChange() { this.updateTagHint(); },
  updateTagHint() {
    const n = document.querySelectorAll('#fTagCols input[name="fTagOpt"]:checked').length;
    const pk = this.state._picker || {};
    const el = document.getElementById("fTagHint");
    if (el) el.textContent = `已勾选 ${n} 个 · ${pk.hint || "多选"}`;
  },
  clearTagFilter() {
    document.querySelectorAll('#fTagCols input[name="fTagOpt"]').forEach(cb => { cb.checked = false; });
    this.updateTagHint();
  },
  // 统一「应用」：按当前上下文分派（筛选 / 批量打标签 / 作品编辑）
  applyTagPicker() {
    const names = [...document.querySelectorAll('#fTagCols input[name="fTagOpt"]:checked')]
      .map(cb => cb.value);
    const pk = this.state._picker;
    const onApply = pk && pk.onApply;
    this.closeTagPanel();
    if (onApply) { onApply(names); return; }
    // 兜底：默认按筛选处理
    this.state._tags = names;
    this.renderSelectedTags();
    this.state.page = 1;
    this.load();
  },
  // 兼容旧调用名（筛选栏「应用」）
  applyTagFilter() { this.applyTagPicker(); },
  removeTagFilter(name) {
    this.state._tags = (this.state._tags || []).filter(t => t !== name);
    if (this.state._picker) this.state._picker.selected = new Set(this.state._tags);
    this.syncTagChecks();
    this.renderSelectedTags();
    this.state.page = 1;
    this.load();
  },
  renderSelectedTags() {
    const box = document.getElementById("fTagSelected");
    const cnt = document.getElementById("fTagCount");
    const sel = this.state._tags || [];
    if (cnt) { cnt.textContent = String(sel.length); cnt.style.display = sel.length ? "inline-block" : "none"; }
    if (!box) return;
    box.innerHTML = sel.length
      ? sel.map(n => `<span class="sel-tag">${esc(n)}<button type="button" class="sel-tag-x" title="移除该标签"
            data-name="${esc(n)}" onclick="app.removeTagFilter(this.dataset.name)">×</button></span>`).join("")
      : '<span class="muted">未选择标签</span>';
  },

  debounce() { clearTimeout(this._t); this._t = setTimeout(() => { this.state.page = 1; this.load(); }, 300); },
  apply() { this.state.page = 1; this.load(); },

  // 年份变化时重置月份（月份下拉始终全量，年份单独作用于查询）
  onYearChange() {
    document.getElementById("fMonth").value = "";
    this.state.page = 1;
    this.load();
  },

  changePageSize() {
    this.state.size = parseInt(document.getElementById("pageSize").value) || 30;
    this.state.page = 1;
    this.load();
  },

  toggleFilters() {
    const el = document.getElementById("filters");
    const btn = document.getElementById("filterToggle");
    const collapsed = el.classList.toggle("collapsed");
    btn.textContent = collapsed ? "筛选 ▸" : "筛选 ▾";
    this.positionTagPanel();   // 筛选栏展开/收起后重新贴靠
  },

  async toggleFav(id, ev) {
    ev.stopPropagation();
    const cur = document.getElementById("favpin-" + id).classList.contains("on");
    await this.setState(id, { favorite: !cur });
    document.getElementById("favpin-" + id).classList.toggle("on", !cur);
  },

  async load(append) {
    const token = ++this.state._reqToken;
    if (append && this.state.loading) return;  // 追加模式防并发（筛选/翻页不拦截）
    this.state.loading = true;
    try {
      const data = await this.api("/api/media?" + this.qs());
      if (token !== this.state._reqToken) return;  // 丢弃过期响应
      this.state.total = data.total;
      if (append) {
        appendGrid(data.items);
      } else {
        renderGrid(data.items);
        window.scrollTo({ top: 0, behavior: "auto" });
      }
      document.getElementById("pageInfo").textContent =
        `第 ${data.page} 页 / 共 ${data.total} 部`;
    } catch (e) {
      if (!append && token === this.state._reqToken)
        document.getElementById("grid").innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
    } finally {
      if (token === this.state._reqToken) {
        this.state.loading = false;
        if (append) setTimeout(() => this.checkLoadMore(), 0);
      }
    }
  },

  nav(d) {
    const maxPage = Math.max(1, Math.ceil(this.state.total / this.state.size));
    const next = Math.min(maxPage, Math.max(1, this.state.page + d));
    if (next === this.state.page) return;
    this.state.page = next;
    this.load(false);
  },

  nextPage() {
    const maxPage = Math.max(1, Math.ceil(this.state.total / this.state.size));
    if (this.state.page >= maxPage || this.state.loading) return;
    this.state.page += 1;
    this.load(true);
  },

  checkLoadMore() {
    const el = document.getElementById("loadMore");
    if (!el || this.state.loading) return;
    const rect = el.getBoundingClientRect();
    const maxPage = Math.max(1, Math.ceil(this.state.total / this.state.size));
    if (this.state.page < maxPage && rect.top < window.innerHeight + 400) {
      this.nextPage();
    }
  },

  async openDetail(id) {
    this.closeTagPanel();
    this.loadKinks();
    const m = await this.api("/api/media/" + id);
    const guard = await this.api("/api/play/" + id + "/meta").catch(() => null);
    document.getElementById("drawer").innerHTML = drawerHTML(m, guard);
    initFrameCovers(document.getElementById("drawer"), { source: "detail" });  // 详情页banner：权威来源
    document.getElementById("overlay").classList.add("show");
  },
  closeDetail() { document.getElementById("overlay").classList.remove("show"); },

  async setState(mid, patch) {
    await this.api("/api/media/" + mid + "/state", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    document.getElementById("detailStatus").value = patch.status ?? document.getElementById("detailStatus").value;
  },

  // 点击星星评分（0.5 步进）
  async setRating(mid, val) {
    await this.setState(mid, { personal_rating: val });
    document.getElementById("myStars").innerHTML = myStarsHTML(mid, val);
    document.getElementById("detailMyRating").textContent = val != null ? val.toFixed(1) : "未评";
  },

  async addTag(mid) {
    const input = document.getElementById("tagInput");
    const name = (input.value || "").trim();
    if (!name) return;
    try {
      const d = await this.api("/api/media/" + mid + "/tags", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      input.value = "";
      renderTagList(mid, d.tags);
    } catch (e) { alert("添加失败：" + e.message); }
  },

  async removeTag(mid, name) {
    try {
      const d = await this.api("/api/media/" + mid + "/tags", {
        method: "DELETE", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      renderTagList(mid, d.tags);
    } catch (e) { alert("删除失败：" + e.message); }
  },

  // ---- 标签联想（题材表优先 + 模糊搜索）----
  async loadKinks() {
    if (this.state._tagdict.length) return this.state._tagdict;
    try {
      const d = await this.api("/api/tagdict");
      this.state._tagdict = d.items || [];
    } catch { this.state._tagdict = []; }
    return this.state._tagdict;
  },

  tagSuggest(mid) {
    const input = document.getElementById("tagInput");
    const box = document.getElementById("tagSuggest");
    const kw = (input.value || "").trim().toLowerCase();
    if (!kw || !box) { if (box) box.style.display = "none"; return; }
    const tagdict = this.state._tagdict || [];
    const hits = tagdict.filter(t => t.toLowerCase().includes(kw));
    if (!hits.length) { box.style.display = "none"; return; }
    box.innerHTML = hits.slice(0, 8).map(t =>
      `<div class="tag-suggest-item" onclick="app.pickTagSuggestion(${mid}, this.textContent)">${esc(t)}</div>`).join("");
    box.style.display = "block";
  },

  pickTagSuggestion(mid, name) {
    const input = document.getElementById("tagInput");
    input.value = name;
    const box = document.getElementById("tagSuggest");
    if (box) box.style.display = "none";
  },

  play(id) {
    window.open("player.html?id=" + id, "_blank");
  },
  openLocal(id) {
    fetch("/api/open-local/" + id, { method: "POST" }).then(r => r.json()).then(d => {
      alert(d.ok ? "已在服务器本地打开播放器" : ("打开失败：" + (d.reason || "")));
    });
  },

  // ---- 白屏管理（运维）----
  async openAdmin() {
    this.closeTagPanel();
    document.getElementById("adminModal").style.display = "flex";
    this.loadCategories();   // 填充扫描/作品管理/作品编辑的分类下拉（同一份分类字典）
    // 重开时主动查询进行中的任务，自动切到对应 Tab 并恢复百分比进度展示
    let j = null;
    try { j = await this.api("/api/admin/task"); } catch { j = null; }
    const k = j && j.running ? j.kind : null;
    const tab = k === "scan" ? "scan"
      : (k === "completion" || k === "cover" || k === "cover-online") ? "complete"
      : "edit";
    this.adminTab(tab);
  },
  closeAdmin() {
    document.getElementById("adminModal").style.display = "none";
    clearInterval(this._jobTimer);
  },
  adminTab(tab) {
    document.querySelectorAll(".admin-tabs .tab").forEach(t =>
      t.classList.toggle("on", t.dataset.tab === tab));
    ["edit", "manage", "complete", "scan", "restart", "tags"].forEach(p =>
      document.getElementById("ap-" + p).style.display = p === tab ? "block" : "none");
    if (tab === "scan") { this.refreshScanStatus(); this.loadCategories(); this.pollJob(["scan"]); }
    if (tab === "manage") { this.manageTab(); }
    if (tab === "complete") {
      this.refreshCompletion(); this.refreshCoverSection(); this.loadReviews();
      this.pollJob(["completion", "cover"]);
    }
    if (tab === "tags") { this.loadTags(); }
  },

  // ---- 标签管理（增删改查）----
  async loadTags() {
    const list = document.getElementById("tagAdminList");
    list.innerHTML = '<div class="muted">加载中…</div>';
    let d;
    try { d = await this.api("/api/admin/tags"); }
    catch (e) { list.innerHTML = `<div class="warn">加载失败：${esc(e.message)}</div>`; return; }
    const items = d.items || [];
    if (!items.length) { list.innerHTML = '<div class="empty">暂无标签</div>'; return; }
    list.innerHTML = `<table class="user-table"><thead><tr><th>ID</th><th>标签</th><th>使用数</th><th>操作</th></tr></thead><tbody>` +
      items.map(t => `<tr>
        <td>${t.id}</td>
        <td><span class="tag-chip">${esc(t.name)}</span></td>
        <td>${t.c}</td>
        <td class="tag-admin-ops">
          <button class="btn" data-name="${esc(t.name)}" onclick="app.tagRenamePrompt(${t.id}, this.dataset.name)">改名</button>
          <button class="btn danger" data-name="${esc(t.name)}" onclick="app.tagDeletePrompt(${t.id}, this.dataset.name, ${t.c || 0})">删除</button>
        </td>
      </tr>`).join("") + `</tbody></table>`;
  },

  async tagCreate() {
    const input = document.getElementById("tagNew");
    const name = (input.value || "").trim();
    if (!name) return alert("请输入标签名");
    try {
      await this.api("/api/admin/tags", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      input.value = "";
      this.loadTags();
    } catch (e) { alert("新增失败：" + e.message); }
  },

  tagRenamePrompt(tid, oldName) {
    const name = prompt("输入新的标签名：", oldName);
    if (name === null) return;
    const v = (name || "").trim();
    if (!v) return;
    this.api("/api/admin/tags/" + tid, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: v }),
    }).then(() => this.loadTags()).catch(e => alert("改名失败：" + e.message));
  },

  async tagDelete(tid, name) {
    if (!confirm(`确认删除标签「${name}」？将同步解除其与所有作品的关联。`)) return;
    try {
      await this.api("/api/admin/tags/" + tid, { method: "DELETE" });
      this.loadTags();
    } catch (e) { alert("删除失败：" + e.message); }
  },

  // ---- 删除标签：防误触（输入标签名解锁 + 后端 confirm_name 校验）----
  tagDeletePrompt(tid, name, count) {
    this.state._delTag = { id: tid, name, count: count || 0 };
    document.getElementById("tdName").textContent = name;
    document.getElementById("tdWarn").textContent =
      `该标签当前关联 ${count || 0} 部作品，删除后将同步解除全部关联，且不可恢复。`;
    const input = document.getElementById("tdInput");
    input.value = "";
    input.placeholder = name;
    document.getElementById("tdBtn").disabled = true;
    document.getElementById("tagDeleteModal").style.display = "flex";
    input.focus();
  },
  closeTagDelete() {
    document.getElementById("tagDeleteModal").style.display = "none";
    this.state._delTag = null;
  },
  tagDeleteCheck() {
    const t = this.state._delTag;
    if (!t) return;
    const v = (document.getElementById("tdInput").value || "").trim();
    document.getElementById("tdBtn").disabled = (v !== t.name);
  },
  async tagDeleteConfirm() {
    const t = this.state._delTag;
    if (!t) return;
    if ((document.getElementById("tdInput").value || "").trim() !== t.name) {
      return alert("输入的标签名不一致，无法删除");
    }
    try {
      const r = await this.api(
        `/api/admin/tags/${t.id}?confirm_name=${encodeURIComponent(t.name)}`,
        { method: "DELETE" });
      this.closeTagDelete();
      alert(`已删除标签「${r.deleted}」并解除 ${r.removed_links} 条关联`);
      this.loadTags();
    } catch (e) { alert("删除失败：" + e.message); }
  },

  // ---- 标签导出 / 导入（纯文本：每行 `id,标签值`；增量 / 覆盖）----
  async exportTags() {
    try {
      const r = await fetch("/api/admin/tags/export");
      if (!r.ok) {
        let detail = "";
        try { detail = (await r.json()).detail || ""; } catch { /* 非 JSON 响应 */ }
        throw new Error(`HTTP ${r.status}${detail ? " · " + detail : ""}`);
      }
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "tags_export.csv";
      a.click();
      URL.revokeObjectURL(a.href);
      this.tagImportMsg("已导出 tags_export.csv（每行：id,标签值）", false);
    } catch (e) {
      this.tagImportMsg("导出失败：" + e.message +
        "（若为 HTTP 404，请先「一键重启」以加载后端新接口）", true);
    }
  },
  tagImportMsg(text, isWarn) {
    const el = document.getElementById("tagImportMsg");
    if (el) { el.textContent = text; el.style.color = isWarn ? "var(--warn)" : ""; }
  },
  async importTags() {
    const f = document.getElementById("tagImportFile").files[0];
    if (!f) return alert("请先选择标签文件（每行：id,标签值）");
    const mode = (document.querySelector('input[name="tagImportMode"]:checked') || {}).value || "merge";
    let confirmText = "";
    if (mode === "overwrite") {
      const t = prompt("覆盖导入会清空现有全部标签与作品关联（服务端会先自动备份到 backups/）。\n请输入确认词 __OVERWRITE__ 以继续：", "");
      if ((t || "").trim() !== "__OVERWRITE__") { this.tagImportMsg("已取消覆盖导入", true); return; }
      confirmText = "__OVERWRITE__";
    }
    this.tagImportMsg("读取文件中…", false);
    // 以「原始字节 → base64」上传，交由服务端探测编码（UTF-8/UTF-8-BOM/GBK/Big5），
    // 避免 ANSI/GBK 文件被浏览器强制按 UTF-8 解码而产生乱码。
    let dataB64 = "";
    try {
      const buf = new Uint8Array(await f.arrayBuffer());
      let bin = "";
      const CH = 0x8000;
      for (let i = 0; i < buf.length; i += CH) {
        bin += String.fromCharCode.apply(null, buf.subarray(i, i + CH));
      }
      dataB64 = btoa(bin);
    } catch (e) {
      this.tagImportMsg("读取文件失败：" + e.message, true);
      return;
    }
    this.tagImportMsg("导入中…", false);
    try {
      const r = await this.api("/api/admin/tags/import", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, data_b64: dataB64, confirm_text: confirmText }),
      });
      const bak = r.backup ? ` · 已备份 ${r.backup.files.join(" + ")}` : "";
      this.tagImportMsg(
        `导入完成（${r.mode === "overwrite" ? "覆盖" : "增量"}｜识别编码 ${r.encoding}）：新增标签 ${r.added_tags}` +
        ` · 跳过 ${r.skipped} · 沿用原ID ${r.kept_ids} · 重分配ID ${r.remapped} · 现有标签 ${r.total_tags}${bak}`,
        false);
      this.loadTags();
    } catch (e) { this.tagImportMsg("导入失败：" + e.message, true); }
  },

  // ---- 标签恢复（误覆盖 / 乱码修复）----
  tagRecoverMsg(text, isWarn) {
    const el = document.getElementById("tagRecoverMsg");
    if (el) { el.textContent = text; el.style.color = isWarn ? "var(--warn)" : ""; }
  },
  async tagsRecoverDiag() {
    this.tagRecoverMsg("诊断中…");
    try {
      const r = await this.api("/api/admin/tags/recover", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: true }),
      });
      this.tagRecoverMsg(
        `乱码标签 ${r.garbled_tags} 个（含关联 ${r.garbled_links}）· 可从简评痕迹恢复的作品 ${r.recoverable_media} 部` +
        ` · 现有标签 ${r.total_tags} · 现有关联 ${r.total_links}`);
    } catch (e) { this.tagRecoverMsg("诊断失败：" + e.message, true); }
  },
  async tagsRecoverRun(actions) {
    const label = actions.includes("drop_garbled") ? "清理乱码标签（含其关联）" : "按简评痕迹重建标签与关联";
    if (!confirm(`确认执行：${label}？此操作会写入数据库。`)) return;
    this.tagRecoverMsg("执行中…");
    try {
      const r = await this.api("/api/admin/tags/recover", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: false, actions }),
      });
      const extra = r.restored_links != null ? ` · 恢复关联 ${r.restored_links}（${r.restored_media} 部作品）` : "";
      this.tagRecoverMsg(
        `已执行：清理乱码标签 ${r.garbled_tags} 个${extra} · 现有标签 ${r.total_tags} · 现有关联 ${r.total_links}`);
      this.loadTags();
    } catch (e) { this.tagRecoverMsg("执行失败：" + e.message, true); }
  },
  async startTagsBackfillForce() {
    if (!confirm("重跑简评打标会忽略「人工编辑保护」，覆盖已标记作品的标签。确认继续？")) return;
    if (!confirm("再次确认：确定要重跑并覆盖？")) return;
    try {
      const res = await this.api("/api/admin/tags-backfill", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: true }),
      });
      if (!res.started) { this.tagRecoverMsg("未启动：" + res.reason, true); return; }
      this.tagRecoverMsg("打标任务已提交（忽略人工保护）…");
      clearInterval(this._tagRecTimer);
      this._tagRecTimer = setInterval(async () => {
        let s;
        try { s = await this.api("/api/admin/tags-backfill/status"); } catch { return; }
        if (s.running) {
          this.tagRecoverMsg(`打标进行中… ${s.done}/${s.total}（当前：${s.current || "-"}）`);
          return;
        }
        clearInterval(this._tagRecTimer);
        const r = s.result || {};
        this.tagRecoverMsg(r.canceled ? "打标已取消" :
          `打标完成：目标 ${r.total} · 已打标 ${r.tagged} · 无命中 ${r.skipped} · 人工保护 ${r.protected}`);
        this.loadTags();
      }, 1500);
    } catch (e) { this.tagRecoverMsg("提交失败：" + e.message, true); }
  },

  // ---- 分类字典（扫描可选分类 / 作品管理 / 首页筛选共用同一份数据）----
  async loadCategories() {
    let items = [];
    try { items = (await this.api("/api/admin/categories")).items || []; } catch { items = []; }
    this.state._cats = items;
    const fill = (id, keepAllLabel, labelAll) => {
      const el = document.getElementById(id);
      if (!el) return;
      const cur = el.value;
      el.innerHTML = `<option value="">${keepAllLabel ? labelAll || "全部分类" : "自动（按目录名）"}</option>`;
      items.forEach(c => {
        const o = document.createElement("option");
        o.value = c.name;
        o.textContent = `${c.name}（${c.c}）`;
        el.appendChild(o);
      });
      if ([...el.options].some(o => o.value === cur)) el.value = cur;
    };
    fill("scanCategory", false);
    fill("aCategory", true);
    fill("mgCategory", true);
    fill("mgMoveTo", false, "");   // 移动目标：仅列出分类，无“全部”
    return items;
  },
  async createCategory() {
    const inp = document.getElementById("mgNewCat");
    const name = (inp.value || "").trim();
    if (!name) return alert("请输入分类名");
    try {
      await this.api("/api/admin/categories", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      inp.value = "";
      await this.loadCategories();
      this.mgMsg(`已新增分类「${name}」`);
    } catch (e) { this.mgMsg("新增失败：" + e.message, true); }
  },
  async renameCategory() {
    const sel = document.getElementById("mgCategory");
    const name = sel.value;
    if (!name) return alert("请先在「当前分类」里选择一个分类");
    const cur = (this.state._cats || []).find(c => c.name === name);
    const neu = prompt(`把分类「${name}」重命名为：`, name);
    if (neu === null) return;
    const v = (neu || "").trim();
    if (!v || v === name) return;
    try {
      const r = await this.api(`/api/admin/categories/${cur.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: v }),
      });
      await this.loadCategories();
      document.getElementById("mgCategory").value = v;
      this.mgMsg(`已重命名，同步更新了 ${r.moved} 部作品的分类`);
      this.manageSearch();
    } catch (e) { this.mgMsg("重命名失败：" + e.message, true); }
  },
  async deleteCategory() {
    const sel = document.getElementById("mgCategory");
    const name = sel.value;
    if (!name) return alert("请先在「当前分类」里选择一个分类");
    const cur = (this.state._cats || []).find(c => c.name === name);
    if (!confirm(`确认删除分类「${name}」？\n（仅允许删除空分类；该分类下还有作品时会拒绝）`)) return;
    try {
      await this.api(`/api/admin/categories/${cur.id}`, { method: "DELETE" });
      await this.loadCategories();
      document.getElementById("mgCategory").value = "";
      this.mgMsg(`已删除分类「${name}」`);
      this.manageSearch();
    } catch (e) { this.mgMsg("删除失败：" + e.message, true); }
  },

  // ---- 作品管理（按分类展示 / 模糊查询 / 批量移动 / 批量删索引）----
  state_mgPicked() {
    if (!this.state._mgPicked) this.state._mgPicked = new Set();
    return this.state._mgPicked;
  },
  mgMsg(text, isWarn) {
    const el = document.getElementById("mgCatMsg");
    if (el) { el.textContent = text; el.style.color = isWarn ? "var(--warn)" : ""; }
  },
  async manageTab() {
    await this.loadCategories();
    this.state._mgPicked = new Set();
    this.state._mgPage = 1;
    await this.manageSearch();
  },
  manageReset() {
    document.getElementById("mgQ").value = "";
    this.state._mgPage = 1;
    this.manageSearch();
  },
  managePage(d) {
    const p = (this.state._mgPage || 1) + d;
    if (p < 1) return;
    this.state._mgPage = p;
    this.manageSearch();
  },
  async manageSearch() {
    const cat = document.getElementById("mgCategory").value;
    const q = document.getElementById("mgQ").value.trim();
    const p = new URLSearchParams();
    if (cat) p.set("category", cat);
    if (q) p.set("q", q);
    p.set("page", this.state._mgPage || 1);
    p.set("size", 50);
    const box = document.getElementById("mgList");
    box.innerHTML = '<div class="muted">加载中…</div>';
    let d;
    try { d = await this.api("/api/admin/media/list?" + p.toString()); }
    catch (e) { box.innerHTML = `<div class="warn">加载失败：${esc(e.message)}</div>`; return; }
    const items = d.items || [];
    this.state._mgItems = items;
    this.state._mgTotal = d.total;
    // 只保留当前列表里仍存在的勾选
    const ids = new Set(items.map(i => i.id));
    [...this.state_mgPicked()].forEach(id => { if (!ids.has(id)) this.state_mgPicked().delete(id); });
    const maxPage = Math.max(1, Math.ceil(d.total / d.size));
    document.getElementById("mgPageInfo").textContent =
      ` 第 ${d.page}/${maxPage} 页 · 共 ${d.total} 部`;
    box.innerHTML = `
      <table class="user-table mg-table">
        <thead><tr>
          <th class="mg-ck"><input type="checkbox" id="mgAll" title="全选当前列表展示的所有作品"
                onchange="app.mgToggleAll(this.checked)"></th>
          <th>索引ID</th><th>分类</th><th>作品名称</th><th>年份</th><th>封面</th>
        </tr></thead>
        <tbody>${items.map(it => `
          <tr>
            <td class="mg-ck"><input type="checkbox" name="mgPick" value="${it.id}"
                  ${this.state_mgPicked().has(it.id) ? "checked" : ""} onchange="app.mgToggle(${it.id}, this.checked)"></td>
            <td>#${it.id}</td>
            <td>${esc(it.category || "")}</td>
            <td class="mg-title">${esc(it.title || "")}</td>
            <td>${it.year ?? ""}</td>
            <td>${it.has_cover ? "有" : "—"}</td>
          </tr>`).join("")}
        </tbody>
      </table>${items.length ? "" : '<div class="empty">该分类/条件下没有作品</div>'}`;
    this.mgUpdateSel();
  },
  mgToggle(id, on) {
    const s = this.state_mgPicked();
    if (on) s.add(id); else s.delete(id);
    this.mgUpdateSel();
    this.mgSyncAll();
  },
  mgToggleAll(on) {
    const s = this.state_mgPicked();
    (this.state._mgItems || []).forEach(it => { if (on) s.add(it.id); else s.delete(it.id); });
    document.querySelectorAll('#mgList input[name="mgPick"]').forEach(cb => { cb.checked = on; });
    this.mgUpdateSel();
  },
  mgSyncAll() {
    const all = document.getElementById("mgAll");
    const items = this.state._mgItems || [];
    if (!all) return;
    all.checked = items.length > 0 && items.every(it => this.state_mgPicked().has(it.id));
    all.indeterminate = !all.checked && items.some(it => this.state_mgPicked().has(it.id));
  },
  mgUpdateSel() {
    const el = document.getElementById("mgSelInfo");
    if (el) el.textContent = `已选 ${this.state_mgPicked().size} 部`;
    this.mgSyncAll();
  },
  async manageMove() {
    const ids = [...this.state_mgPicked()];
    if (!ids.length) return alert("请先勾选要移动的作品（可用表头复选框全选当前列表）");
    const to = document.getElementById("mgMoveTo").value;
    if (!to) return alert("请选择目标分类（或先在上面新增分类）");
    if (!confirm(`确认把选中的 ${ids.length} 部作品移动到分类「${to}」？\n（只改索引归属，不动磁盘文件）`)) return;
    try {
      const r = await this.api("/api/admin/media/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "move", ids, category: to }),
      });
      this.mgMsg(`已移动 ${r.moved} 部到「${r.category}」`);
      this.state._mgPicked = new Set();
      await this.loadCategories();
      this.manageSearch();
    } catch (e) { this.mgMsg("移动失败：" + e.message, true); }
  },
  async manageDelete() {
    const ids = [...this.state_mgPicked()];
    if (!ids.length) return alert("请先勾选要删除的作品（可用表头复选框全选当前列表）");
    if (!confirm(`确认从项目中删除选中的 ${ids.length} 条作品索引？\n\n注意：只删除索引与关联数据，磁盘上的视频文件不会被删除；\n如误删，重新扫描对应文件夹即可恢复。`)) return;
    try {
      const r = await this.api("/api/admin/media/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete", ids }),
      });
      this.mgMsg(`已删除 ${r.deleted} 条索引（磁盘文件未改动）`);
      this.state._mgPicked = new Set();
      await this.loadCategories();
      this.manageSearch();
    } catch (e) { this.mgMsg("删除失败：" + e.message, true); }
  },

  // ---- 作品管理：批量打标签（复用标签气泡，多选）----
  manageTagPick(ev) {
    const ids = [...this.state_mgPicked()];
    if (!ids.length) return alert("请先勾选要打标签的作品（可用表头复选框全选当前列表）");
    const anchor = (ev && (ev.currentTarget || ev.target)) || null;
    this.openTagPicker(ev, {
      kind: "bulk",
      anchor,
      selected: [],                      // 批量场景：初始不勾选（本次要追加的标签）
      title: `批量打标签（已选 ${ids.length} 部）`,
      hint: "追加模式，保留各作品原有标签；每作品最多 10 个",
      onApply: (names) => this.bulkTagApply(ids, names),
    });
  },
  async bulkTagApply(ids, names) {
    if (!names.length) { this.mgMsg("未选择任何标签，已取消", true); return; }
    if (!confirm(`确认给选中的 ${ids.length} 部作品追加 ${names.length} 个标签？\n\n` +
      `· 追加模式：保留各作品原有标签，只新增所选标签；\n` +
      `· 每作品最多 10 个标签，超出部分会被跳过。`)) return;
    try {
      const r = await this.api("/api/admin/media/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "tag", mode: "append", ids, tags: names }),
      });
      this.mgMsg(`已为 ${r.affected} 部作品追加标签（新增关联 ${r.added_links}` +
        `${r.capped ? " · 超上限跳过 " + r.capped : ""}）`);
      this.manageSearch();
    } catch (e) { this.mgMsg("批量打标签失败：" + e.message, true); }
  },

  // ---- 作品编辑：点击标签输入框弹出气泡（多选，应用后写回输入框）----
  editTagsPick(ev) {
    const inp = document.getElementById("aTags");
    if (!inp) return;
    const cur = (inp.value || "").split(/\s+/).filter(Boolean);
    this.openTagPicker(ev, {
      kind: "edit",
      anchor: inp,
      selected: cur,
      title: "选择本作品的标签",
      hint: "应用后写回输入框，可继续手动编辑",
      onApply: (names) => {
        inp.value = names.slice(0, 10).join(" ");
        this.closeTagPanel();
      },
    });
  },

  // ---- 作品编辑：筛选 / 重置 / 下载 ----
  _adminQuery() {
    const p = new URLSearchParams();
    const q = document.getElementById("aQ").value.trim(); if (q) p.set("q", q);
    const ct = document.getElementById("aCategory").value; if (ct) p.set("category", ct);
    const cv = document.getElementById("aCover").value; if (cv !== "") p.set("has_cover", cv);
    const sy = document.getElementById("aSyn").value; if (sy !== "") p.set("has_synopsis", sy);
    const ts = document.getElementById("aTagState").value; if (ts !== "") p.set("has_tags", ts);
    const tk = document.getElementById("aTagKw").value.trim(); if (tk) p.set("tag", tk);
    const yr = document.getElementById("aYearF").value.trim(); if (yr) p.set("year", yr);
    return p;
  },
  async adminSearch() {
    const p = this._adminQuery();
    let d = { items: [] };
    try { d = await this.api("/api/admin/media/search?" + p.toString()); }
    catch (e) { document.getElementById("aPickInfo").textContent = "查询失败：" + e.message; return; }
    const items = d.items || [];
    const sel = document.getElementById("aPick");
    sel.innerHTML = items.map(m => `<option value="${m.id}">#${m.id} · ${m.year || "----"} ${esc(m.title)}${m.studio ? "（" + esc(m.studio) + "）" : ""}</option>`).join("");
    document.getElementById("aPickInfo").textContent =
      `共 ${items.length} 条结果 · 缺封面 ${items.filter(i => !i.has_cover).length} · 缺简介 ${items.filter(i => !i.has_synopsis).length}` +
      `（「下载索引ID.txt」将按此查询结果导出）`;
    document.getElementById("aForm").style.display = "none";
  },
  adminReset() {
    ["aQ", "aCategory", "aCover", "aSyn", "aTagState", "aTagKw", "aYearF"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
    this.adminSearch();
  },
  async downloadIds() {
    // 导出的是**当前查询结果**的索引ID；未设置任何条件即导出全部作品
    const p = this._adminQuery();
    const r = await fetch("/api/admin/media/ids.txt?" + p.toString());
    if (!r.ok) return alert("下载失败（HTTP " + r.status + "）");
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "media_ids.txt";
    a.click();
    URL.revokeObjectURL(a.href);
    const n = (await blob.text()).trim().split(/\r?\n/).filter(Boolean).length;
    document.getElementById("aPickInfo").textContent =
      `已导出 ${n} 个索引ID（按当前查询条件）`;
  },
  async adminLoad(mid) {
    const m = await this.api("/api/admin/media/" + mid);
    document.getElementById("aId").value = m.id;
    document.getElementById("aIdShow").value = m.id;
    document.getElementById("aTitle").value = m.title || "";
    document.getElementById("aTitleJp").value = m.title_jp || "";
    document.getElementById("aStudio").value = m.studio || "";
    document.getElementById("aYear").value = m.year ?? "";
    document.getElementById("aDate").value = m.publish_date || "";
    document.getElementById("aSub").checked = !!m.subtitle;
    const syn = m.synopsis || "";
    document.getElementById("aSynopsis").value = syn;
    document.getElementById("aSynCount").textContent = syn.length;
    document.getElementById("aTags").value = (m.tags || []).join(" ");
    document.getElementById("aPath").value = m.file_path || "";
    document.getElementById("aPoster").value = m.poster_path || "";
    document.getElementById("aRating").value = m.rating_norm ?? "";
    document.getElementById("aRatingRaw").value = m.rating_raw || "";
    document.getElementById("candidate").innerHTML = "";
    document.getElementById("aForm").style.display = "block";
  },
  async adminSave() {
    const mid = document.getElementById("aId").value;
    const body = {
      title: document.getElementById("aTitle").value,
      title_jp: document.getElementById("aTitleJp").value,
      studio: document.getElementById("aStudio").value,
      year: document.getElementById("aYear").value || null,
      publish_date: document.getElementById("aDate").value || null,
      subtitle: document.getElementById("aSub").checked ? 1 : 0,
      synopsis: document.getElementById("aSynopsis").value,
      tags: document.getElementById("aTags").value.split(/\s+/).filter(Boolean).slice(0, 10),
      file_path: document.getElementById("aPath").value,
      poster_path: document.getElementById("aPoster").value || null,
      rating_norm: document.getElementById("aRating").value === "" ? null : parseFloat(document.getElementById("aRating").value),
      rating_raw: document.getElementById("aRatingRaw").value,
    };
    await this.api("/api/admin/media/" + mid + "/edit", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    alert("已保存");
    this.adminLoad(mid);
  },
  async metaRefresh() {
    const mid = document.getElementById("aId").value;
    if (!mid) return alert("请先查找并选择一部作品");
    const c = await this.api("/api/admin/media/" + mid + "/metadata-refresh", { method: "POST" });
    const cand = c.candidate;
    if (cand.error) {
      document.getElementById("candidate").innerHTML =
        `<div class="warn">联网补全失败：${esc(cand.error)}</div>`;
      return;
    }
    const editableSyn = cand.editable && cand.editable.synopsis;
    const editableTags = cand.editable && cand.editable.tags;
    document.getElementById("candidate").innerHTML = `
      <div class="candbox">
        <h4>联网候选（${esc(cand.source || "-")}）</h4>
        <p><b>简介建议：</b>${esc((cand.synopsis || "(无)"))} ${editableSyn ? "" : '<span class="warn-inline">（已人工编辑，不可覆盖）</span>'}</p>
        <p><b>标签建议（≤10）：</b>${esc((cand.tags || []).join(" / ") || "(无)")} ${editableTags ? "" : '<span class="warn-inline">（已人工编辑，不可覆盖）</span>'}</p>
        <button class="btn" onclick="app.metaApply()" ${editableSyn || editableTags ? "" : "disabled"}>采用并入库（审定）</button>
      </div>`;
  },
  async metaApply() {
    const mid = document.getElementById("aId").value;
    const body = {
      synopsis: document.getElementById("aSynopsis").value,
      tags: document.getElementById("aTags").value.split(/\s+/).filter(Boolean).slice(0, 10),
    };
    await this.api("/api/admin/media/" + mid + "/metadata-apply", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    alert("已入库");
    this.adminLoad(mid);
  },
  async adminRestart() {
    const text = document.getElementById("rConfirm").value;
    if (text !== "__RESTART__") { document.getElementById("rMsg").textContent = "确认文本不正确，未执行"; return; }
    if (!confirm("再次确认：确定要一键重启服务吗？")) return;
    const tk = await this.api("/api/admin/restart-token");
    await this.api("/api/admin/restart", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_text: text, token: tk.token }),
    });
    document.getElementById("rMsg").textContent = "已提交，服务即将重启（约数秒）。";
  },

  // ---- 管理中心（用户 / 用户组，占位）----
  openManage() {
    document.getElementById("manageModal").style.display = "flex";
    this.manageTab("users");
  },
  closeManage() { document.getElementById("manageModal").style.display = "none"; },
  manageTab(tab) {
    document.querySelectorAll("#manageModal .admin-tabs .tab").forEach(t =>
      t.classList.toggle("on", t.dataset.tab === tab));
    document.getElementById("mp-users").style.display = tab === "users" ? "block" : "none";
    document.getElementById("mp-groups").style.display = tab === "groups" ? "block" : "none";
    if (tab === "users") this.loadUsers();
  },
  async loadUsers() {
    const tbody = document.querySelector("#userList tbody");
    tbody.innerHTML = '<tr><td colspan="4" class="muted">加载中…</td></tr>';
    const d = await this.api("/api/admin/users").catch(() => ({ items: [] }));
    const items = d.items || [];
    tbody.innerHTML = items.map(u =>
      `<tr><td>${u.id}</td><td>${esc(u.name)}</td><td>${esc(u.role)}</td><td>${esc(u.created_at || "")}</td></tr>`).join("")
      || '<tr><td colspan="4" class="muted">暂无用户</td></tr>';
  },

  // ---- 扫描（指定路径直接扫 / 留空全盘需防误操作确认）----
  startScan() {
    const path = document.getElementById("scanPath").value.trim();
    if (!path) { document.getElementById("fullScanConfirm").style.display = "block"; return; }
    this.submitScan("path", path);
  },
  submitScan(scope, path) {
    const catEl = document.getElementById("scanCategory");
    const category = catEl ? catEl.value : "";
    this.api("/api/admin/scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, path: path || null, confirm_full: scope === "full", category: category || null }),
    }).then(res => {
      if (!res.started) { document.getElementById("scanProgressText").textContent = "未启动：" + res.reason; return; }
      document.getElementById("scanProgressText").textContent =
        "已提交扫描…" + (res.category ? `（记入分类：${res.category}）` : "（分类按目录名自动判定）");
      this.pollJob(["scan"]);
    }).catch(e => { document.getElementById("scanProgressText").textContent = "未启动：" + e.message; });
  },
  confirmFullScan() { this.submitScan("full", null); this.hideFullScan(); },
  hideFullScan() { document.getElementById("fullScanConfirm").style.display = "none"; },
  async terminateTask(cancelUrl, statusUrl, btnIds, statusId) {
    const btns = (Array.isArray(btnIds) ? btnIds : [btnIds]).map(id => document.getElementById(id)).filter(Boolean);
    btns.forEach(b => { b.classList.add("loading"); b.disabled = true; });
    const statusEl = statusId ? document.getElementById(statusId) : null;
    let r;
    try {
      r = await this.api(cancelUrl, { method: "POST" });
    } catch (e) {
      btns.forEach(b => { b.classList.remove("loading"); b.disabled = false; });
      return;
    }
    if (statusEl) statusEl.textContent = r.msg;
    if (!r.ok) {
      btns.forEach(b => { b.classList.remove("loading"); b.disabled = false; });
      return;
    }
    // 轮询直到任务真正停止，再恢复按钮
    while (true) {
      await new Promise(res => setTimeout(res, 500));
      const s = await this.api(statusUrl).catch(() => ({ running: false }));
      if (!s.running) break;
    }
    btns.forEach(b => { b.classList.remove("loading"); b.disabled = false; });
  },

  async cancelScan() {
    await this.terminateTask("/api/admin/scan/cancel", "/api/admin/scan/status",
      "scanCancelBtn", "scanProgressText");
  },
  async refreshScanStatus() {
    let s;
    try { s = await this.api("/api/admin/scan/status"); } catch { return; }
    const el = document.getElementById("barFill");
    if (!s.running && s.kind == null) { el.style.width = "0%"; el.style.animation = "none"; return; }
    const pct = s.total ? (s.done / s.total * 100).toFixed(0) + "%" : (s.running ? "45%" : "100%");
    el.style.width = pct;
    el.style.animation = s.running ? "pulse 1.2s infinite" : "none";
    const txt = document.getElementById("scanProgressText");
    if (s.running) {
      txt.textContent = `正在扫描… ${s.done}/${s.total}（当前：${esc(s.current || "-")}）`;
      return;
    }
    if (s.result) {
      const r = s.result;
      const pend = (r.rating_pending || []).map(p => `· ${esc(p.title)} → ${p.score}`).join("\n");
      document.getElementById("scanResult").textContent =
        `扫描 ${r.scanned} · 新增 ${r.added} · 更新 ${r.updated} · 移除 ${r.removed}` +
        (r.canceled ? "\n（已取消）" : "") +
        (r.error ? "\n错误：" + r.error : "") +
        (pend ? "\n\n待人工匹配 " + r.rating_pending.length + " 条评分：\n" + pend : "\n评分已全部回填");
      txt.textContent = r.canceled ? "已取消" : "完成";
    } else { txt.textContent = "待机"; }
  },

  // ---- 联网补全（合并：可选上传索引ID 限定范围 / 全量缺失补全）----
  async startCompletion() {
    const ids = this.state._ids || [];
    const body = ids.length ? { ids } : {};
    const res = await this.api("/api/admin/completion", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.started) { document.getElementById("completionStatus").textContent = "未启动：" + res.reason; return; }
    document.getElementById("completionStatus").textContent =
      ids.length ? `已提交按ID补全（${ids.length} 部）…` : "已提交全量补全…";
    this.pollJob(["completion"]);
  },
  parseIdsFile() {
    const f = document.getElementById("idsFile").files[0];
    if (!f) return alert("请先选择 txt 文件（作品编辑页「下载索引ID.txt」得到）");
    const reader = new FileReader();
    reader.onload = () => {
      // 严格逐行取首列整数（"id,标题" 或纯 id 行），杜绝标题中的年份/集数混入
      const ids = [];
      for (const line of String(reader.result).split(/\r?\n/)) {
        const m = line.match(/^\s*(\d+)\s*(?:,|$)/);
        if (m) ids.push(Number(m[1]));
      }
      const uniq = [...new Set(ids)].filter(Boolean);
      this.state._ids = uniq;
      const list = document.getElementById("idsList");
      list.style.display = "block";
      list.value = uniq.join(", ");
      document.getElementById("idsCount").textContent =
        uniq.length ? `已解析 ${uniq.length} 个索引ID（将严格按此范围补全）` : "未解析到有效索引ID";
    };
    reader.readAsText(f, "utf-8");
  },
  clearIds() {
    this.state._ids = [];
    const fileInput = document.getElementById("idsFile");
    if (fileInput) fileInput.value = "";
    const list = document.getElementById("idsList");
    if (list) { list.style.display = "none"; list.value = ""; }
    document.getElementById("idsCount").textContent = "尚未解析 ID 文件（不上传 = 全量缺失补全）";
  },
  async cancelCompletion() {
    await this.terminateTask("/api/admin/completion/cancel", "/api/admin/completion/status",
      "compCancelBtn", "completionStatus");
  },
  async refreshCompletion() {
    let s;
    try { s = await this.api("/api/admin/completion/status"); } catch { return; }
    const el = document.getElementById("compFill");
    if (!s.running && s.kind == null) { el.style.width = "0%"; el.style.animation = "none"; }
    else {
      el.style.width = s.total ? (s.done / s.total * 100).toFixed(0) + "%" : (s.running ? "45%" : "100%");
      el.style.animation = s.running ? "pulse 1.2s infinite" : "none";
    }
    const txt = document.getElementById("completionStatus");
    if (s.running) { txt.textContent = `正在补全… ${s.done}/${s.total}（当前：${esc(s.current || "-")}）`; return; }
    if (s.result) {
      const r = s.result;
      txt.textContent = r.canceled ? "已取消" : "完成";
      const miss = (r.missing || []).length ? `\n缺失 ${r.missing.length} 个ID` : "";
      document.getElementById("completionResult").textContent =
        `目标 ${r.total} · 已补全 ${r.filled}` +
        (r.failed_reason ? `\n失败 ${r.failed}（示例：${esc(r.failed_reason)}）` : (r.failed ? `\n失败 ${r.failed}` : "")) +
        `\n未命中 ${r.skipped}` + miss +
        (r.date_filled ? `\n补发布年月 ${r.date_filled}（其中年份 ${r.year_filled ?? 0}）` : "") +
        (r.tags_online ? `\n联网标签 ${r.tags_online}` : "") +
        (r.tags_local ? `\n简评打标 ${r.tags_local}` : "") +
        (r.canceled ? "\n（已取消）" : "") +
        `\n来源缀联：本地 → 百度 → sample（tag 按题材过滤，≤10）`;
    } else { txt.textContent = "待机"; }
  },

  // ---- 封面补全（本地匹配 / 联网搜索）----
  async startCover() {
    const res = await this.api("/api/admin/cover", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!res.started) { document.getElementById("coverStatus").textContent = "未启动：" + res.reason; return; }
    document.getElementById("coverStatus").textContent = "已提交封面补全…";
    this.pollJob(["cover"]);
  },
  async startCoverOnline() {
    const ids = this.state._ids || [];
    if (!ids.length) {
      alert("「联网搜索封面」需要先确定范围（索引ID），当前尚未解析任何 ID。操作步骤：\n\n" +
        "① 切到「作品编辑」标签页 → 点「下载索引ID.txt」（得到 id 清单，可自行删减为只含需要补封面的作品）；\n" +
        "② 回到本页「A · 全量联网补全」区域 → 选择该文件 → 点「解析ID」；\n" +
        "③ 看到「已解析 N 个索引ID」后，再点「联网搜索封面（B）」即可。\n\n" +
        "说明：联网搜封面会逐个作品联网抓取下载，限定范围可避免误扫全库、耗时过长。");
      return;
    }
    const res = await this.api("/api/admin/cover-online", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    if (!res.started) { document.getElementById("coverStatus").textContent = "未启动：" + res.reason; return; }
    document.getElementById("coverStatus").textContent =
      `已提交联网封面补全（按已解析的 ${ids.length} 个索引ID）…`;
    this.pollJob(["cover-online"]);
  },
  // ③ 指定目录补全：从人工指定的本地目录取图片与缺封面作品按标题匹配
  async startCoverDir() {
    const inp = document.getElementById("coverDirPath");
    const path = (inp.value || "").trim();
    if (!path) return alert("请填写一个本地目录路径（例如 D:\\...\\封面素材目录）");
    const st = document.getElementById("coverStatus");
    st.textContent = "正在按指定目录匹配封面…";
    try {
      const r = await this.api("/api/admin/cover-dir", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      st.textContent = "完成";
      document.getElementById("coverResult").textContent =
        `指定目录：${r.dir}\n目录内图片 ${r.images} 张 · 待匹配作品 ${r.candidates} 部\n` +
        `已匹配并写回封面 ${r.matched} 部 · 未匹配 ${r.no_match} · 已有封面跳过 ${r.skipped_has_cover}` +
        ` · 人工封面保护跳过 ${r.skipped_edited}\n（只记录路径引用，未复制/移动任何文件）`;
    } catch (e) {
      st.textContent = "失败";
      document.getElementById("coverResult").textContent = "指定目录补全失败：" + e.message;
    }
  },
  // ④ 截图视频封面：用视频预览帧当封面（不生成、不存储任何图片文件）
  async startVideoFrameCover() {
    const ids = this.state._ids || [];
    if (!confirm("把「视频预览帧」作为封面（不存储任何图片文件）？\n\n" +
      (ids.length ? `将只处理已解析的 ${ids.length} 个索引ID。` : "当前未解析索引ID，将对全部缺封面作品生效。"))) return;
    const st = document.getElementById("coverStatus");
    try {
      const r = await this.api("/api/admin/cover-video-frame", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "set", ids }),
      });
      st.textContent = "完成";
      document.getElementById("coverResult").textContent =
        `已标记 ${r.marked} 部作品用「视频预览帧」当封面（未存储任何图片文件）\n` +
        `跳过：已有本地封面 ${r.skipped_has_cover} · 人工封面保护 ${r.skipped_edited}`;
    } catch (e) { st.textContent = "失败"; document.getElementById("coverResult").textContent = "失败：" + e.message; }
  },
  async clearVideoFrameCover() {
    if (!confirm("取消「用视频预览帧当封面」标记？（不影响已存在的实体封面文件）")) return;
    const ids = this.state._ids || [];
    try {
      const r = await this.api("/api/admin/cover-video-frame", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "clear", ids }),
      });
      document.getElementById("coverStatus").textContent = "完成";
      document.getElementById("coverResult").textContent = `已取消标记 ${r.cleared} 部`;
    } catch (e) { document.getElementById("coverResult").textContent = "失败：" + e.message; }
  },
  // ⑤ 服务端抽帧：用 ffmpeg 把 cover_mode=video_frame 的作品抽帧落盘小图，写 poster_path，
  // 前端改走普通图片加载（首屏更快）。
  async startFrameBackfill() {
    const ids = this.state._ids || [];
    if (!confirm("用 ffmpeg 服务端预抽帧，把「视频预览帧封面」落盘为小图？\n\n" +
      (ids.length ? `将只处理已解析的 ${ids.length} 个索引ID。` : "未解析索引ID，将处理全部符合条件的作品。") +
      "\n抽帧后前端改走普通图片加载，首屏更快。")) return;
    const st = document.getElementById("coverStatus");
    try {
      const res = await this.api("/api/admin/cover-frame", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      if (!res.started) { st.textContent = "未启动：" + res.reason; return; }
      st.textContent = "已提交服务端抽帧任务…";
      this.pollJob(["frame"]);
    } catch (e) { st.textContent = "失败"; document.getElementById("coverResult").textContent = "失败：" + e.message; }
  },
  async cancelCover() {
    if (!confirm("终止封面任务？将清理所有待审定候选记录与 cover_cache 孤儿文件。")) return;
    // cover 与 cover-online 共用终止按钮，按当前任务类型路由到正确的接口
    const j = await this.api("/api/admin/task").catch(() => ({}));
    const kind = j.kind;
    const cancelUrl = kind === "cover-online" ? "/api/admin/cover-online/cancel" : "/api/admin/cover/cancel";
    const statusUrl = kind === "cover-online" ? "/api/admin/cover-online/status" : "/api/admin/cover/status";
    await this.terminateTask(cancelUrl, statusUrl, "coverCancelBtn", "coverStatus");
  },
  // 封面区统一渲染器：本地匹配(cover)与联网搜索(cover-online)共用一组进度元素，
  // 数据源为 /api/admin/task（返回当前任务完整状态），避免双写互相覆盖。
  async refreshCoverSection() {
    let s;
    try { s = await this.api("/api/admin/task"); } catch { return; }
    const el = document.getElementById("coverFill");
    const txt = document.getElementById("coverStatus");
    if (s.kind !== "cover" && s.kind !== "cover-online" && s.kind !== "frame") {
      // 无封面类任务：待机（或其他任务进行中，本区不打扰）
      el.style.width = "0%";
      el.style.animation = "none";
      txt.textContent = s.running ? "待机（其他任务进行中）" : "待机";
      return;
    }
    el.style.width = s.total ? (s.done / s.total * 100).toFixed(0) + "%" : (s.running ? "45%" : "100%");
    el.style.animation = s.running ? "pulse 1.2s infinite" : "none";
    if (s.running) {
      txt.textContent = (s.kind === "cover-online" ? "联网搜封面" :
                         s.kind === "frame" ? "服务端抽帧" : "正在补封面") +
        `… ${s.done}/${s.total}（${esc(s.current || "-")}）`;
      return;
    }
    if (s.result) {
      const r = s.result;
      txt.textContent = r.canceled ? "已取消" : "完成";
      if (s.kind === "frame") {
        document.getElementById("coverResult").textContent =
          `目标 ${r.total} · 已抽帧 ${r.extracted} · 失败 ${r.failed} · 跳过 ${r.skipped}` +
          (r.canceled ? "\n（已取消）" : "\n（已写回封面，前端改走普通图片加载，首屏更快）");
      } else if (s.kind === "cover") {
        document.getElementById("coverResult").textContent =
          `索引封面 ${r.indexed ?? "?"} · 目标 ${r.total} · 补齐 ${r.filled} · 未命中 ${r.skipped}` +
          (r.canceled ? "\n（已取消）" : "");
      } else {
        const lines = [`范围 ${r.scope_ids ?? "?"} 个索引ID · 目标 ${r.total} · 补齐 ${r.filled} · 未找到 ${r.notfound} · 下载失败 ${r.failed} · 跳过 ${r.skipped}`];
        for (const d of r.detail || []) {
          lines.push(`${d.ok ? "✓" : "✗"} [${d.id}] ${d.title} ${d.ok ? "→ " + d.cover : "(" + (d.error || "") + ")"}`);
        }
        document.getElementById("coverResult").textContent = lines.join("\n");
      }
    } else { txt.textContent = "待机"; }
  },

  // ---- 人工审定（双栏：左作品名列表 + 右候选修改区）----
  // 有效行为：① 点击候选图片（选中该图）；② 勾选「本行有效」按钮。
  // 任一有效行为 → 左栏「待修改」→「已修改」；批量提交成功后显示已完成并从左栏移除。
  async loadReviews() {
    const left = document.getElementById("reviewLeftList");
    const detail = document.getElementById("reviewDetail");
    if (left) left.innerHTML = '<div class="muted">加载中…</div>';
    if (detail) detail.innerHTML = "";
    let d;
    try { d = await this.api("/api/admin/cover-reviews?status=pending&source=last_cover_online"); }
    catch (e) { if (left) left.innerHTML = `<div class="warn">加载失败：${esc(e.message)}</div>`; return; }
    this.state._reviews = d.items || [];
    this.state._revIndex = this.state._reviews.length ? 0 : -1;
    this.state._revDone = new Set();
    this.state._revPick = new Map();     // rid → {idx, url}
    const hint = document.getElementById("reviewScopeHint");
    if (hint) {
      hint.textContent = d.filtered
        ? `已按步骤 B「联网搜索封面」最近一次范围筛选（范围 ${d.scope_ids} 个索引ID` +
          `${d.scope_ts ? " · " + d.scope_ts : ""}，排除范围外旧候选 ${d.dropped} 条）`
        : (d.scope_ids
            ? `步骤 B 最近一次范围 ${d.scope_ids} 个索引ID${d.scope_ts ? " · " + d.scope_ts : ""}（当前候选均在其中）`
            : "尚无「联网搜索封面」记录，未按步骤 B 范围筛选（显示全部待审定候选）");
    }
    this.renderReviewLeft();
    this.renderReviewDetail();
    this.updateBatchCount();
  },
  // 重置列表：只清空当前视图（不删库），可再点「刷新列表」重新获取
  resetReviewList() {
    this.state._reviews = [];
    this.state._revIndex = -1;
    this.state._revPick = new Map();
    this.renderReviewLeft();
    this.renderReviewDetail();
    this.updateBatchCount();
    const hint = document.getElementById("reviewScopeHint");
    if (hint) hint.textContent = "已重置当前视图（未改动数据库）— 点「刷新列表」可重新获取。";
  },
  _reviewDoneSet() { if (!this.state._revDone) this.state._revDone = new Set(); return this.state._revDone; },
  _pickMap() { if (!this.state._revPick) this.state._revPick = new Map(); return this.state._revPick; },
  _findReview(rid) { return (this.state._reviews || []).find(r => r.id === rid); },
  _reviewCountText() {
    const n = (this.state._reviews || []).length;
    const m = this._reviewDoneSet().size;
    return `共 ${n} 条待审定 · 本次已完成 ${m}`;
  },
  renderReviewLeft() {
    const left = document.getElementById("reviewLeftList");
    if (!left) return;
    const items = this.state._reviews || [];
    const doneSet = this._reviewDoneSet();
    const picks = this._pickMap();
    if (!items.length) { left.innerHTML = '<div class="empty">没有待审定的候选</div>'; }
    else {
      left.innerHTML = items.map((r, i) => {
        const state = doneSet.has(r.id) ? "done" : (picks.has(r.id) ? "picked" : "todo");
        const text = state === "done" ? "已完成" : (state === "picked" ? "已修改" : "待修改");
        const on = i === this.state._revIndex;
        return `<div class="rev-item state-${state}${on ? " on" : ""}" onclick="app.selectReview(${i})">
          <span class="rev-item-title">#${r.media_id} ${esc(r.title)}</span>
          <span class="rev-item-state">${text}</span>
        </div>`;
      }).join("");
    }
    const rc = document.getElementById("reviewCount");
    if (rc) rc.textContent = this._reviewCountText();
  },
  renderReviewDetail() {
    const el = document.getElementById("reviewDetail");
    if (!el) return;
    const i = this.state._revIndex;
    const items = this.state._reviews || [];
    if (i < 0 || i >= items.length) { el.innerHTML = '<div class="muted">请在左侧选择一部作品</div>'; return; }
    const rev = items[i];
    const all = rev.candidates || [];
    const cands = all.slice(0, 10);           // 仅展示前 10 个素材
    const conf = rev.confidence != null ? Math.round(rev.confidence * 100) + "%" : "-";
    const pick = this._pickMap().get(rev.id);
    const thumbs = cands.map((_u, idx) => `
      <div class="rev-thumb${pick && pick.idx === idx ? " sel" : ""}" onclick="app.pickThumb(${rev.id}, ${idx})"
           title="点击选择该候选图（视为有效行为）">
        <img loading="lazy" src="/api/admin/cover-reviews/${rev.id}/preview/${idx}"
             alt="候选${idx + 1}" onerror="this.parentElement.classList.add('broken')">
      </div>`).join("");
    el.innerHTML = `
      <div class="rev-detail-head">
        <b>#${rev.media_id} ${esc(rev.title)}</b>
        ${rev.title_jp && rev.title_jp !== rev.title ? `<span class="jp">${esc(rev.title_jp)}</span>` : ""}
        <span class="muted">置信度 ${conf}</span>
        ${rev.post_url ? `<a href="${esc(rev.post_url)}" target="_blank" rel="noopener">来源帖子</a>` : ""}
        ${rev.error ? `<span class="warn-inline">${esc(rev.error)}</span>` : ""}
        <div class="rev-nav">
          <button class="btn" onclick="app.reviewRescan(${rev.id})">重搜…</button>
          <button class="btn danger" onclick="app.reviewReject(${rev.id})">拒绝吗</button>
          <button class="btn" onclick="app.reviewNav(-1)" ${i > 0 ? "" : "disabled"}>上一条</button>
          <span class="muted">${i + 1}/${items.length}</span>
          <button class="btn" onclick="app.reviewNav(1)" ${i < items.length - 1 ? "" : "disabled"}>下一条</button>
        </div>
      </div>
      <div class="rev-thumbs">${cands.length ? thumbs : '<span class="muted">无候选图（可用「重搜…」换关键词）</span>'}</div>
      ${all.length > 10 ? `<div class="muted">…另有 ${all.length - 10} 个候选未展示</div>` : ""}
      <div class="muted">点击候选图即视为有效行为 → 左栏变为「已修改」，再点「批量提交修改」提交。</div>`;
  },
  selectReview(i) {
    this.state._revIndex = i;
    this.renderReviewLeft();
    this.renderReviewDetail();
    const node = document.querySelector(".rev-item.on");
    if (node) node.scrollIntoView({ block: "nearest" });
  },
  reviewNav(d) {
    const n = (this.state._reviews || []).length;
    if (!n) return;
    const next = Math.max(0, Math.min(n - 1, (this.state._revIndex ?? 0) + d));
    this.selectReview(next);
  },
  // 有效行为：点击候选图片 → 选中该图并标记本行为「已修改」
  pickThumb(rid, idx) {
    const rev = this._findReview(rid);
    if (!rev) return;
    const url = (rev.candidates || [])[idx];
    const cur = this._pickMap().get(rid);
    if (cur && cur.idx === idx) this._pickMap().delete(rid);   // 再次点击取消选择
    else this._pickMap().set(rid, { idx, url });
    this.renderReviewLeft();
    this.renderReviewDetail();
    this.updateBatchCount();
  },
  updateBatchCount() {
    const n = this._pickMap().size;
    const btn = document.getElementById("batchReviewBtn");
    if (btn) btn.textContent = `批量提交修改（${n}）`;
  },
  // 成功处理后的行：显示已完成 → 从左侧列表移除
  _removeReviews(ids) {
    const set = new Set(ids);
    const done = this._reviewDoneSet();
    const picks = this._pickMap();
    const prev = this.state._revIndex;
    set.forEach(id => { done.add(id); picks.delete(id); });
    this.state._reviews = (this.state._reviews || []).filter(r => !set.has(r.id));
    const max = this.state._reviews.length - 1;
    this.state._revIndex = max < 0 ? -1 : Math.min(Math.max(prev, 0), max);
    this.renderReviewLeft();
    this.renderReviewDetail();
    this.updateBatchCount();
  },
  async batchAcceptReviews() {
    const picks = [...this._pickMap()];
    if (!picks.length) return alert("请先在右栏点击选择候选图片，或勾选「本行有效」（可多行一起提交）");
    const accept = picks.map(([rid, p]) => ({ review_id: rid, url: p.url || null }));
    if (!confirm(`确认批量提交修改 ${accept.length} 条（未指定图片的按默认候选下载）？`)) return;
    const r = await this.api("/api/admin/cover-reviews/batch-submit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept, reject: [] }),
    });
    const failedIds = new Set((r.failed || []).map(f => f.review_id));
    const okIds = accept.map(a => a.review_id).filter(id => !failedIds.has(id));
    this._removeReviews(okIds);
    alert(`已提交 ${r.accepted} 条` + (failedIds.size ? `，失败 ${failedIds.size} 条（保留在列表中）` : ""));
  },
  async reviewReject(rid) {
    if (!confirm("确认拒绝并删除该候选？")) return;
    await this.api(`/api/admin/cover-reviews/${rid}/reject`, { method: "POST" });
    this._removeReviews([rid]);
  },
  async reviewRescan(rid) {
    const kw = prompt("输入自定义搜索关键词（留空则按标题自动派生）：", "");
    if (kw === null) return;
    const r = await this.api(`/api/admin/cover-reviews/${rid}/rescan`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword: kw }),
    });
    if (r.review) {
      const idx = this.state._reviews.findIndex(x => x.id === rid);
      if (idx >= 0) { this.state._reviews[idx] = r.review; this.renderReviewDetail(); }
    } else { this.loadReviews(); }
  },

  // ---- 消息通知气泡（服务告警 + 任务完成通知）----
  startNotifLoop() {
    if (this._notifTimer) return;
    this.refreshNotifDot();
    this._notifTimer = setInterval(() => this.refreshNotifDot(), 5000);
  },
  async refreshNotifDot() {
    try {
      const d = await this.api("/api/admin/notifications/unread-count");
      const dot = document.getElementById("notifDot");
      if (dot) dot.classList.toggle("show", (d.count || 0) > 0);
    } catch { /* 忽略（无权限或服务未就绪） */ }
  },
  async toggleNotif(ev) {
    ev && ev.stopPropagation();
    const p = document.getElementById("notifPanel");
    const open = p.classList.toggle("open");
    if (open) this.loadNotifList();
  },
  closeNotif() { document.getElementById("notifPanel").classList.remove("open"); },
  async loadNotifList() {
    const box = document.getElementById("notifList");
    box.innerHTML = '<div class="muted" style="padding:10px">加载中…</div>';
    let d;
    try { d = await this.api("/api/admin/notifications?limit=50"); }
    catch (e) { box.innerHTML = `<div class="warn">加载失败：${esc(e.message)}</div>`; return; }
    const items = d.items || [];
    if (!items.length) { box.innerHTML = '<div class="muted" style="padding:12px">暂无消息</div>'; return; }
    box.innerHTML = items.map(n => `
      <div class="notif-item${n.is_read ? "" : " unread"}">
        <div class="t">${esc(n.title)}</div>
        <div class="meta">
          <span>${esc(n.created_at || "")}</span>
          ${n.is_read ? "" : `<button onclick="app.notifRead(${n.id})">已读</button>`}
          <a href="logs.html?id=${n.id}" target="_blank">查看详情</a>
        </div>
      </div>`).join("");
  },
  async notifRead(nid) {
    await this.api(`/api/admin/notifications/${nid}/read`, { method: "POST" });
    this.loadNotifList();
    this.refreshNotifDot();
  },
  async notifReadAll() {
    await this.api("/api/admin/notifications/read-all", { method: "POST" });
    this.loadNotifList();
    this.refreshNotifDot();
  },

  // ---- 任务轮询（多任务并行刷新各自的进度条）----
  pollJob(kinds) {
    clearInterval(this._jobTimer);
    const arr = Array.isArray(kinds) ? kinds : [kinds];
    const handlers = {
      scan: () => this.refreshScanStatus(),
      completion: () => this.refreshCompletion(),
      cover: () => this.refreshCoverSection(),
      "cover-online": () => this.refreshCoverSection(),
      frame: () => this.refreshCoverSection(),
    };
    const tick = () => arr.forEach(k => handlers[k] && handlers[k]());
    tick();
    this._jobTimer = setInterval(tick, 1500);
  },

  metaLoad() {
    // 标签气泡脱离 .sticky-top 的层叠上下文，挂到 body 下：确保渲染在最顶层
    const tp = document.getElementById("fTagPanel");
    if (tp && tp.parentElement !== document.body) document.body.appendChild(tp);
    this.api("/api/stats").then(d => {
      this.state.categories = d.categories.map(c => c.category);
      const years = d.years.map(y => y.year).filter(Boolean).sort();
      fillOptions("fCat", this.state.categories);
      fillOptions("fYear", years);
      const months = (d.months || []).map(m => m.month).filter(Boolean).sort((a, b) => a - b);
      fillOptions("fMonth", months);
      // 标签筛选气泡：分栏展示（6~8 栏，每栏最多 12 行）+ 已选标签栏位
      this.renderTagCols(d.tags || []);
      this.renderSelectedTags();
      // 补充字典中暂无关联的标签（保证气泡在无关联数据时也非空）
      this.api("/api/tags").then(t => { this.renderTagCols(t.items || []); this.syncTagChecks(); })
        .catch(() => {});
      const fav = document.getElementById("statChip");
      fav.textContent = `${d.total} 部 · 平均 ${d.rating_avg ?? "-"}`;
    });
    this.load();
    this.startNotifLoop();
    // 点击气泡外关闭（消息气泡 / 标签气泡）
    document.addEventListener("click", (e) => {
      const wrap = document.querySelector(".notif-wrap");
      if (wrap && !wrap.contains(e.target)) this.closeNotif();
      const tw = document.getElementById("fTagWrap");
      const tp = document.getElementById("fTagPanel");
      const pk = this.state._picker;
      const anchor = pk && pk.anchor;
      // 标签气泡已挂到 body 下（脱离 sticky 栈上下文）；锚点可能是筛选栏按钮 /
      // 作品管理的批量打标签按钮 / 作品编辑的标签输入框，需一并判断命中。
      if (tw && !tw.contains(e.target) && !(tp && tp.contains(e.target))
          && !(anchor && anchor.contains(e.target))) this.closeTagPanel();
    });
    // 气泡为 fixed 定位：窗口尺寸/滚动变化时重新贴靠按钮
    window.addEventListener("resize", () => this.positionTagPanel());
    window.addEventListener("scroll", () => this.positionTagPanel(), { passive: true });
    if (!this._io && "IntersectionObserver" in window) {
      this._io = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting) this.nextPage();
      }, { rootMargin: "400px" });
      this._io.observe(document.getElementById("loadMore"));
    }
  },
};

function fillOptions(id, values) {
  const el = document.getElementById(id);
  const cur = el.value;
  const keep = el.options[0] ? el.options[0].cloneNode(true) : null;
  el.innerHTML = "";
  if (keep) el.appendChild(keep);
  values.forEach(v => { const o = document.createElement("option"); o.value = v; o.textContent = v; el.appendChild(o); });
  if ([...el.options].some(o => o.value === cur)) el.value = cur;
}

function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

function renderTagList(mid, tags) {
  const box = document.getElementById("tagList");
  if (!box) return;
  box.innerHTML = tags.length
    ? tags.map(t => `<span class="tag-chip">${esc(t)}<button class="tag-x" data-name="${esc(t)}" onclick="app.removeTag(${mid}, this.dataset.name)">×</button></span>`).join(" ")
    : '<i>无</i>';
}

function starStr(score) {
  if (score == null) return "";
  const full = Math.floor(score), frac = score - full;
  return "★".repeat(full) + (frac >= 0.5 ? "½" : "");
}

function myStarsHTML(mid, val) {
  const v = val == null ? 0 : val;
  let html = "";
  for (let i = 1; i <= 5; i++) {
    let cls = "star big";
    if (v >= i) cls += " full";
    else if (v >= i - 0.5) cls += " half";
    html += `<span class="${cls}" title="点击星星评分（左半=+0.5）">
      <i class="zl" onclick="app.setRating(${mid}, ${i - 0.5})">★</i>
      <i class="zr" onclick="app.setRating(${mid}, ${i})">★</i>
    </span>`;
  }
  return html;
}

function ratingBadge(m) {
  if (m.rating_norm == null) return `<span class="rbadge none">—</span>`;
  const level = m.rating_norm >= 4.5 ? "hi" : m.rating_norm >= 4 ? "mid" : "lo";
  return `<span class="rbadge ${level}" title="${esc(m.rating_raw || "")}">${m.rating_norm.toFixed(1)}</span>`;
}

function yearMonth(m) {
  if (m.year) return m.year + (m.publish_date ? "-" + m.publish_date.slice(5, 7) : "");
  return m.publish_date ? m.publish_date.slice(0, 7) : "";
}

// ============================================================
// 视频帧封面缩略图引擎（低内存方案，参考 Windows 缩略图机制）
// · 卡片/详情页里只放静态 <img>，绝不插入几十个 <video>（内存低、点击正常）；
// · 用一个全局单例的离屏 <video> 串行抓帧 → dataURL（只存内存，不落盘）；
// · 抓到的帧写入会话缓存，并同步填充页面上同 id 的所有 img；
// · 详情页(detail)是权威来源：网格(grid)结果永不覆盖 detail，网格可复用 detail 帧。
// ============================================================

// 1x1 浅灰占位图（卡片初始显示，抓到帧后由引擎替换）
const THUMB_PLACEHOLDER = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

function posterHTML(m, cls, onerr) {
  if (m.cover_mode === "video_frame") {
    // 静态 <img> 占位：真正的帧由「离屏单例 video」串行抓取后回填（见下方引擎）
    return `<img class="frame-cover${cls ? " " + cls : ""}" data-id="${m.id}" data-pending="1"
              src="${THUMB_PLACEHOLDER}" alt="" loading="lazy">`;
  }
  return `<img loading="lazy" class="${cls || ""}" src="/api/poster/${m.id}" ${onerr || ""}>`;
}

// 会话缓存：source = "detail"（详情页 banner，权威）/ "grid"（网格卡）
// 规则：① 网格结果**永不覆盖详情页结果**；② 非黑优先；③ 同类画面比评分；
//       ④ 写入后同步刷新页面上同 id 的所有 .frame-cover 元素。
function stashFrame(id, url, score, isBlack, source) {
  const st = app.state;
  st._frames = st._frames || {};
  st._scores = st._scores || {};
  st._blacks = st._blacks || {};
  st._src = st._src || {};
  const has = !!st._frames[id];
  const prevSrc = st._src[id];
  const prevBlack = !!st._blacks[id];
  const prevScore = st._scores[id] != null ? st._scores[id] : -1;
  if (has) {
    if (prevSrc === "detail" && source === "grid") return st._frames[id];
    if (prevBlack === isBlack) {
      if (prevScore >= score) return st._frames[id];
    } else if (!prevBlack) {
      return st._frames[id];
    }
  }
  st._frames[id] = url;
  st._scores[id] = score;
  st._blacks[id] = !!isBlack;
  st._src[id] = source;
  document.querySelectorAll(`.frame-cover[data-id="${id}"]`).forEach(el => {
    el.src = url;
    delete el.dataset.pending;
    el.dataset.lowbright = isBlack ? "1" : "";
  });
  return url;
}

// 时间点选择（纯函数，便于测试）：**非黑优先 → 其次比画面评分**
function pickBestTime(cands) {
  let best = null;
  for (const c of cands) {
    if (!c) continue;
    if (!best) { best = c; continue; }
    const bB = !!best.isBlack, cB = !!c.isBlack;
    if (bB !== cB) { if (!cB) best = c; continue; }   // 非黑者优先
    if (c.score > best.score) best = c;
  }
  return best;
}

function _nextFrame() {
  return new Promise(r => {
    if (typeof requestAnimationFrame !== "function") return setTimeout(r, 16);
    requestAnimationFrame(r);   // 等一帧即可：seeked 后帧已解码，少等一帧更快
  });
}

// ---- 离屏 <video> 池 + 并发抓帧 + IndexedDB 持久化 ----
// 内存仍低：仅 3 个 1x1 离屏 video；串行改为 3 并发，加载提速约 3 倍。
const THUMB_CONCURRENCY = 3;

function _makeThumbVideo() {
  const v = document.createElement("video");
  v.muted = true;
  v.autoplay = true;          // muted 静音自动播放被允许，确保主动解码（否则 seek 可能不产帧）
  v.playsInline = true;
  v.preload = "metadata";
  // 放在视口左上角 1x1 且几乎透明（不能移出视口，否则浏览器可能因「视口外不解码」而抓不到帧）
  v.style.cssText = "position:fixed;left:0;top:0;width:1px;height:1px;opacity:0.001;pointer-events:none;z-index:-1;";
  document.body.appendChild(v);
  return v;
}

const _thumbVideos = [];
function _thumbVideoAt(i) {
  if (!_thumbVideos[i]) _thumbVideos[i] = _makeThumbVideo();
  return _thumbVideos[i];
}

const _thumbQueue = [];        // {id, source}
let _thumbActive = 0;

// ---- IndexedDB 缩略图持久化：网格帧抓一次、二次进入秒开 ----
const IDB_NAME = "vm_frames";
const IDB_STORE = "frames";
let _idb = null;
function _idbOpen() {
  if (_idb) return Promise.resolve(_idb);
  return new Promise((resolve) => {
    try {
      const req = indexedDB.open(IDB_NAME, 1);
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(IDB_STORE)) {
          req.result.createObjectStore(IDB_STORE, { keyPath: "id" });
        }
      };
      req.onsuccess = () => { _idb = req.result; resolve(_idb); };
      req.onerror = () => { _idb = null; resolve(null); };
    } catch { resolve(null); }
  });
}
function _idbGet(id) {
  return _idbOpen().then(db => new Promise((resolve) => {
    if (!db) return resolve(null);
    try {
      const rq = db.transaction(IDB_STORE, "readonly").objectStore(IDB_STORE).get(String(id));
      rq.onsuccess = () => resolve(rq.result || null);
      rq.onerror = () => resolve(null);
    } catch { resolve(null); }
  }));
}
function _idbPut(id, url, score) {
  _idbOpen().then(db => {
    if (!db) return;
    try {
      db.transaction(IDB_STORE, "readwrite").objectStore(IDB_STORE)
        .put({ id: String(id), url, score });
    } catch { /* 持久化失败不影响本次展示 */ }
  });
}

function enqueueFrameThumb(id, source) {
  const st = app.state;
  st._frames = st._frames || {};
  st._src = st._src || {};
  // 缓存命中：grid 可直接复用（含 detail 帧）；detail 只复用 detail 自己的帧
  if (st._frames[id]) {
    const cachedSrc = st._src[id];
    if (source === "grid" || cachedSrc === "detail") {
      _fillThumb(id);
      return;
    }
    // source=detail 但缓存是 grid 帧 → 继续抓 detail 权威帧
  }
  const ex = _thumbQueue.find(x => x.id === id);
  if (ex) {
    if (source === "detail") ex.source = "detail";   // 升级优先级
    return;
  }
  _thumbQueue.push({ id, source });
  _thumbPump();
}

function _fillThumb(id) {
  const st = app.state;
  if (!st._frames || !st._frames[id]) return;
  const url = st._frames[id];
  const low = st._blacks && st._blacks[id] ? "1" : "";
  document.querySelectorAll(`.frame-cover[data-id="${id}"]`).forEach(el => {
    el.src = url;
    delete el.dataset.pending;
    if (low) el.dataset.lowbright = "1"; else delete el.dataset.lowbright;
  });
}

function _thumbPump() {
  while (_thumbActive < THUMB_CONCURRENCY && _thumbQueue.length) {
    const job = _thumbQueue.shift();
    const slot = _thumbActive;
    _thumbActive++;
    const v = _thumbVideoAt(slot);
    (async () => {
      try {
        const st = app.state;
        st._src = st._src || {};
        st._frames = st._frames || {};
        // 处理前若已存在 detail 权威帧且这是 grid → 直接复用
        if (job.source === "grid" && st._src[job.id] === "detail" && st._frames[job.id]) {
          _fillThumb(job.id);
          return;
        }
        const res = await _grabFrame(v, job.id, job.source);
        if (res) {
          stashFrame(job.id, res.url, res.score, res.isBlack, job.source);
          // grid 非黑帧 → 持久化，二次进入秒开
          if (job.source === "grid" && !res.isBlack) {
            _idbPut(job.id, res.url, res.score);
          }
        }
      } finally {
        _thumbActive--;
        _thumbPump();   // 继续拉取队列
      }
    })();
  }
}

// 用离屏 video 抓该作品的一帧（选帧规则同前：非黑优先 → 比评分）
async function _grabFrame(v, id, source) {
  const width = source === "detail" ? 720 : 256;   // 详情页更高清，网格用小缩略图
  try {
    v.pause();
    v.removeAttribute("src");
    v.load();
    v.src = `/api/play/${id}`;
    if (!(await _videoMetaReady(v))) return null;

    const dur = isFinite(v.duration) ? (v.duration || 0) : 0;
    const probe = document.createElement("canvas");
    probe.width = 160; probe.height = 90;
    const pctx = probe.getContext("2d", { willReadFrequently: true });
    const analyse = () => {
      try {
        pctx.drawImage(v, 0, 0, 160, 90);
        return frameStats(pctx.getImageData(0, 0, 160, 90).data, 160, 90);
      } catch { return null; }
    };

    // 大胆抽帧：首帧取 20%（跳过片头黑场/片头曲），一次 seek 直接抓，不再密集抽样。
    // 仅当首帧接近全黑时，才再试中段一次（最多 2 次 seek），保证不黑又不拖慢。
    const first = dur ? Math.min(Math.max(dur * 0.2, 2), Math.max(dur - 1, 2)) : 2;
    let bestT = first, bestScore = 0, bestIsBlack = false;
    const s = await _seekAnalyse(v, first, analyse);
    if (!s) return null;
    bestScore = s.score;
    bestIsBlack = s.isBlack;
    if (s.isBlack && dur > 5) {
      const t2 = dur ? Math.min(Math.max(dur * 0.5, 2), Math.max(dur - 2, 2)) : 2;
      if (Math.abs(t2 - first) > 0.5) {
        const s2 = await _seekAnalyse(v, t2, analyse);
        if (s2 && !s2.isBlack) { bestT = t2; bestScore = s2.score; bestIsBlack = false; }
      }
    }
    if (Math.abs((v.currentTime || 0) - bestT) > 0.2) {
      await _seekTo(v, bestT);
      await _nextFrame();
    }
    // 固定宽度、等比、不放大、不裁切
    const vw = v.videoWidth || 16, vh = v.videoHeight || 9;
    const w = Math.round(Math.min(width, vw));
    const h = Math.max(1, Math.round(w * vh / vw));
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    c.getContext("2d").drawImage(v, 0, 0, w, h);
    return { url: c.toDataURL("image/jpeg", 0.8), score: bestScore, isBlack: bestIsBlack };
  } catch {
    return null;
  } finally {
    try { v.pause(); v.removeAttribute("src"); v.load(); } catch {}
  }
}

function _videoMetaReady(v) {
  if (v.readyState >= 1 && isFinite(v.duration)) return Promise.resolve(true);
  return new Promise(res => {
    let done = false;
    const ok = (val) => { if (!done) { done = true; res(val); } };
    v.addEventListener("loadedmetadata", () => ok(true), { once: true });
    v.addEventListener("error", () => ok(false), { once: true });
    setTimeout(() => ok(v.readyState >= 1), 8000);
  });
}

function _seekTo(v, t) {
  return new Promise(res => {
    let done = false;
    const ok = (val) => { if (!done) { done = true; res(val); } };
    v.addEventListener("seeked", () => ok(true), { once: true });
    setTimeout(() => ok(false), 3000);
    try { v.currentTime = t; } catch { ok(false); }
  });
}

async function _seekAnalyse(v, t, analyse) {
  if (!(await _seekTo(v, t))) return null;
  await _nextFrame();     // 等解码后的画面真正可绘制，避免拿到上一帧（常为黑帧）
  return analyse();
}

// ---- 入口：渲染后收集 frame-cover 元素，进入视口才入队抓帧 ----
// opts.source: "detail"（详情页，权威、只用自己的结果）/ "grid"（网格卡，可复用详情页结果）
function initFrameCovers(root, opts) {
  if (!root) return;
  const source = (opts && opts.source) || "grid";
  const st = app.state;
  st._frames = st._frames || {};
  root.querySelectorAll(".frame-cover[data-id]").forEach(el => {
    const id = el.dataset.id;
    if (!id) return;
    if (st._frames[id]) {
      const cachedSrc = st._src && st._src[id];
      const usable = source === "detail" ? cachedSrc === "detail" : true;
      if (usable) { _fillThumb(id); return; }
    }
    if (source === "grid") {
      // 网格卡：先查 IndexedDB 秒回填（二次进入），未命中再入队抓帧
      _idbGet(id).then(rec => {
        if (rec && rec.url) {
          stashFrame(id, rec.url, rec.score || 0, false, "grid");
        } else {
          observeThumb(el, id, source);
        }
      });
    } else {
      observeThumb(el, id, source);
    }
  });
}

let _thumbIO = null;
function observeThumb(el, id, source) {
  if (typeof IntersectionObserver !== "function") { enqueueFrameThumb(id, source); return; }
  if (!_thumbIO) {
    _thumbIO = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (!e.isIntersecting) return;
        _thumbIO.unobserve(e.target);
        enqueueFrameThumb(e.target.dataset.id, e.target.dataset.src || "grid");
      });
    }, { rootMargin: "900px" });
  }
  el.dataset.src = source;
  _thumbIO.observe(el);
}

// 画面统计：均值/标准差/饱和度/边缘密度 → 综合"是否有画面"的评分
function frameStats(d, w, h) {
  let sum = 0, sum2 = 0, sat = 0, edge = 0, bright = 0, n = w * h;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      const r = d[i], g = d[i + 1], b = d[i + 2];
      const lum = 0.299 * r + 0.587 * g + 0.114 * b;
      sum += lum; sum2 += lum * lum;
      if (lum > 40) bright++;
      const mx = r > g ? (r > b ? r : b) : (g > b ? g : b);
      const mn = r < g ? (r < b ? r : b) : (g < b ? g : b);
      sat += mx ? (mx - mn) / mx : 0;
      if (x > 0) {
        const j = i - 4;
        edge += Math.abs(lum - (0.299 * d[j] + 0.587 * d[j + 1] + 0.114 * d[j + 2]));
      }
    }
  }
  const mean = sum / n;
  const std = Math.sqrt(Math.max(0, sum2 / n - mean * mean));
  const satAvg = sat / n;
  const edgeAvg = edge / n;
  const brightRatio = bright / n;
  // 「黑色占大部分 / 亮度很低」的判定：均亮极低，或几乎无亮点像素
  const isBlack = mean < 28 || brightRatio < 0.06;
  // 评分：对比度 + 饱和度 + 边缘丰富度；对过暗/过曝惩罚、偏向中间亮度
  let score = (std / 64) + (satAvg / 0.5) * 0.8 + (edgeAvg / 24);
  score *= 1 - Math.min(Math.abs(mean - 110) / 160, 0.8);
  if (isBlack) score *= 0.15;
  return { mean, std, sat: satAvg, edge: edgeAvg, brightRatio, isBlack, score };
}

function cardHTML(m) {
  return `
    <div class="card" onclick="app.openDetail(${m.id})">
      <div class="thumb">
        ${posterHTML(m, "", "onerror=\"this.parentElement.classList.add('noimg');this.remove()\"")}
        <div class="cap">
          <div class="cap-id">#${m.id} · ${esc(yearMonth(m))}</div>
          <div class="cap-title">${esc(m.title)}</div>
        </div>
        <span id="favpin-${m.id}" class="favpin ${m.user.favorite ? "on" : ""}" title="点赞/收藏"
              onclick="app.toggleFav(${m.id}, event)">★</span>
        ${ratingBadge(m)}
      </div>
    </div>`;
}

function renderGrid(items) {
  const g = document.getElementById("grid");
  if (!items.length) { g.innerHTML = `<div class="empty">没有匹配的作品</div>`; return; }
  g.innerHTML = items.map(cardHTML).join("");
  initFrameCovers(g);   // 视频帧封面：渲染后按需截取预览帧（仅内存，不落盘）
}

function appendGrid(items) {
  const g = document.getElementById("grid");
  if (!items.length) return;
  g.insertAdjacentHTML("beforeend", items.map(cardHTML).join(""));
  initFrameCovers(g);   // 视频帧封面：渲染后按需截取预览帧（仅内存，不落盘）
}

function drawerHTML(m, guard) {
  const st = m.user.status;
  const brackets = (m.meta && m.meta.brackets) || "";
  const res = (String(brackets).match(/\b\d{3,4}p\b/i) || [null])[0];
  const vtype = `${String(m.file_ext || "").toUpperCase()}${res ? " · " + res.toUpperCase() : ""}` || "-";
  const tags = m.tags || [];
  return `
    <div class="drawer-head">
      <button class="btn ghost x" onclick="app.closeDetail()">✕</button>
    </div>
    <div class="drawer-body">
      ${posterHTML(m, "poster", "onerror=\"this.style.display='none'\"")}
      <div class="info-box">
        <div class="info-row"><span class="k">评分</span><span class="v">${m.rating_norm != null ? `<b class="rating-big">${m.rating_norm.toFixed(1)}</b>` : "暂无"}</span></div>
        <div class="info-row"><span class="k">名称</span><span class="v">${esc(m.title)}${m.title_jp && m.title_jp !== m.title ? `<div class="jp">${esc(m.title_jp)}</div>` : ""}</span></div>
        <div class="info-row"><span class="k">年月</span><span class="v">${esc(yearMonth(m) || "-")}</span></div>
        <div class="info-row"><span class="k">制作组</span><span class="v">${esc(m.studio || "-")}</span></div>
        <div class="info-row"><span class="k">标签</span><span class="v" id="tagList">${tags.length ? tags.map(t => `<span class="tag-chip">${esc(t)}<button class="tag-x" data-name="${esc(t)}" onclick="app.removeTag(${m.id}, this.dataset.name)">×</button></span>`).join(" ") : '<i>无</i>'}</span></div>
        <div class="info-row"><span class="k">视频类型</span><span class="v">${esc(vtype)}</span></div>
        <div class="info-row"><span class="k">索引ID</span><span class="v">#${m.id}</span></div>
        <div class="info-row"><span class="k">收藏</span><span class="v">${m.favorite_count ?? 0} 人</span></div>
      </div>
      <div class="tag-add">
        <div class="tag-add-box">
          <input id="tagInput" placeholder="添加标签（联想题材表，≤10个）" maxlength="20"
                 oninput="app.tagSuggest(${m.id})" onkeydown="if(event.key==='Enter')app.addTag(${m.id})">
          <div id="tagSuggest" class="tag-suggest"></div>
        </div>
        <button class="btn" onclick="app.addTag(${m.id})">添加</button>
      </div>
      <div class="rates">
        <span>我的评分：<b id="detailMyRating">${m.user.personal_rating != null ? m.user.personal_rating.toFixed(1) : "未评"}</b></span>
        <span id="myStars">${myStarsHTML(m.id, m.user.personal_rating)}</span>
      </div>
      <div class="actions">
        <button class="btn primary" onclick="app.play(${m.id})">在线播放</button>
        <button class="btn" onclick="app.openLocal(${m.id})">本地打开</button>
        <a class="btn ghost" href="/api/download/${m.id}" download>下载</a>
      </div>
      ${guard && guard.guard && guard.guard.oversized ? `<div class="warn">⚠ ${esc(guard.guard.advice)}（${(guard.guard.size / 1073741824).toFixed(1)}GB）</div>` : ""}
      <div class="state-editor">
        <select id="detailStatus" onchange="app.setState(${m.id}, {status:this.value})">
          ${["未看", "想看", "在看", "看完"].map(s => `<option ${st === s ? "selected" : ""}>${s}</option>`).join("")}
        </select>
        <label>我的评分 <input type="number" min="0" max="5" step="0.5" id="pr" value="${m.user.personal_rating ?? ""}" onchange="app.setState(${m.id},{personal_rating:parseFloat(this.value)||null})"></label>
        <label class="fav"><input type="checkbox" id="fav" ${m.user.favorite ? "checked" : ""} onchange="app.setState(${m.id},{favorite:this.checked})"> 收藏</label>
        <textarea placeholder="备注…" onchange="app.setState(${m.id},{note:this.value})">${esc(m.user.note || "")}</textarea>
      </div>
      <div class="path">${esc(m.file_path)}</div>
    </div>`;
}
document.addEventListener("DOMContentLoaded", () => app.metaLoad());
