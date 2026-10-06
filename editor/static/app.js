/* ════════════════════════════════════════════════════════
   MANGA AI EDITOR — DOM-based Editor Logic
   ════════════════════════════════════════════════════════ */

function rgbToHex(val) {
  if (!val) return '#000000';
  if (val.startsWith('#')) return val.toLowerCase();
  const m = val.match(/rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/);
  if (m) {
    const r = parseInt(m[1]).toString(16).padStart(2, '0');
    const g = parseInt(m[2]).toString(16).padStart(2, '0');
    const b = parseInt(m[3]).toString(16).padStart(2, '0');
    return `#${r}${g}${b}`;
  }
  return val;
}

// Arabic block detection: Hayah shapes Arabic natively, but its Latin
// glyphs are ~28% narrower than the site font — so Latin-only texts
// render with a comic/sans Latin chain at the same px size.
function hasArabicChars(s) {
  return /[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]/.test(s || '');
}
function displayFontFor(text, preferred) {
  if (hasArabicChars(text)) return `"${preferred || 'Hayah'}", "Traditional Arabic", serif`;
  return '"Anime Ace", "Comic Sans MS", "Segoe UI", Tahoma, sans-serif';
}

// ─── STATE ──────────────────────────────────────────────
let chapterData = null;
let currentSlug = '';
let currentChapter = '';
let currentPage = 1;
let totalPages = 1;
let isDirty = false;
let currentTool = 'select';
let selectedTextEl = null;
let saveTimeout = null;
let viewMode = 'webtoon';
let editorMode = 'review';
let currentZoom = 1;
let textOverlaysById = {};
let pageOffsets = [];
let pageBlocks = [];
let isSaving = false;
let savePromise = null;
let fontWheelResizeMode = false;
const layoutPrefsKey = 'manga-editor-layout-v1';
let lastExportedText = '';
let lastExportedFilename = 'chapter.txt';

// ─── VIEW-ONLY INTERCEPTOR (PUT/DELETE) ───────────────────
const _origFetch = window.fetch.bind(window);
window.fetch = function(url, opts = {}) {
  try {
    const method = ((opts && opts.method) || 'GET').toUpperCase();
    const u = typeof url === 'string' ? url : (url && url.url) || '';
    if (editorMode === 'view' && (method === 'PUT' || method === 'DELETE') && u.includes('/api/chapter')) {
      toast('وضع المشاهدة: القراءة فقط — التعديل معطّل', 'warning');
      return Promise.resolve(new Response(JSON.stringify({ status: 'blocked', message: 'view-only' }), {
        status: 403, headers: { 'Content-Type': 'application/json' },
      }));
    }
  } catch (e) {}
  return _origFetch(url, opts);
};

function isViewOnly() { return editorMode === 'view'; }
function guardViewOnly() {
  if (isViewOnly()) { toast('وضع المشاهدة: القراءة فقط — التعديل معطّل', 'warning'); return true; }
  return false;
}

// ─── EDITOR MODES ─────────────────────────────────────────
function setEditorMode(mode) {
  if (!['review', 'view', 'edit'].includes(mode)) return;
  editorMode = mode;
  document.querySelectorAll('.editor-mode-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById(`editor-mode-${mode}`);
  if (btn) btn.classList.add('active');
  document.body.classList.toggle('mode-view', mode === 'view');
  document.body.classList.toggle('mode-review', mode === 'review');
  document.body.classList.toggle('mode-edit', mode === 'edit');
  const labels = { review: 'وضع المراجعة', view: 'وضع المشاهدة (قراءة فقط)', edit: 'وضع التحرير الكامل' };
  toast(labels[mode], 'info');
  const rs = document.getElementById('review-section');
  if (rs) rs.style.display = mode === 'view' ? 'none' : '';
}

function approveSelected(status) {
  if (isViewOnly()) { toast('وضع المشاهدة: القراءة فقط', 'warning'); return; }
  const ov = getSelectedOverlay();
  if (!ov) { toast('اختر نصاً أولاً', 'warning'); return; }
  ov.data.approved = status;
  if (ov.el) {
    ov.el.classList.toggle('approved', status === 'approved');
    ov.el.classList.toggle('needs-fix', status === 'needs_fix');
  }
  updateReviewLabel(ov.data);
  markDirty();
  toast(status === 'approved' ? 'تم قبول النص ✓' : 'تم تعليم النص: يحتاج إصلاح ⚠', 'success');
}

function updateReviewLabel(t) {
  const lbl = document.getElementById('review-status-label');
  if (!lbl) return;
  if (!t) { lbl.textContent = 'الحالة: بانتظار المراجعة'; return; }
  lbl.textContent = t.approved === 'approved' ? 'الحالة: مقبول ✓'
    : t.approved === 'needs_fix' ? 'الحالة: يحتاج إصلاح ⚠'
    : 'الحالة: بانتظار المراجعة';
}

// ─── UNDO/REDO ──────────────────────────────────────────
const undoStack = [];
const redoStack = [];
const MAX_UNDO = 50;

function captureSnapshot() {
  if (!chapterData) return null;
  return JSON.parse(JSON.stringify(chapterData.pages));
}

function pushUndo() {
  const snap = captureSnapshot();
  if (!snap) return;
  undoStack.push(snap);
  if (undoStack.length > MAX_UNDO) undoStack.shift();
  redoStack.length = 0;
}

function undo() {
  if (undoStack.length === 0) { toast('لا توجد خطوة للعودة', 'info'); return; }
  const currentSnap = captureSnapshot();
  if (currentSnap) redoStack.push(currentSnap);
  const prev = undoStack.pop();
  chapterData.pages = prev;
  if (viewMode === 'single') renderSinglePage(currentPage);
  else renderWebtoon();
  markDirty();
  toast('تراجع', 'info');
}

function redo() {
  if (redoStack.length === 0) { toast('لا توجد خطوة للتقدم', 'info'); return; }
  const currentSnap = captureSnapshot();
  if (currentSnap) undoStack.push(currentSnap);
  const next = redoStack.pop();
  chapterData.pages = next;
  if (viewMode === 'single') renderSinglePage(currentPage);
  else renderWebtoon();
  markDirty();
  toast('إعادة', 'info');
}

// ─── INIT ───────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  document.getElementById('app-loader').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  restoreLayoutPrefs();
  showChapterSelector();
  document.body.classList.add('mode-review');

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    const activeTag = (document.activeElement?.tagName || '').toUpperCase();
    const isEditing = activeTag === 'INPUT' || activeTag === 'TEXTAREA' || document.activeElement?.isContentEditable;
    if ((e.key === 'Delete' || e.key === 'Backspace') && !isEditing) {
      if (selectedTextEl) {
        deleteTextObject();
        e.preventDefault();
      }
    }
    if (e.ctrlKey && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      saveAll();
    }
    if (e.ctrlKey && e.key === 'z' && !e.shiftKey) {
      e.preventDefault();
      undo();
    }
    if ((e.ctrlKey && e.key === 'y') || (e.ctrlKey && e.shiftKey && e.key === 'z')) {
      e.preventDefault();
      redo();
    }
    if (e.ctrlKey && (e.key === 't' || e.key === 'T')) {
      if (selectedTextEl) {
        e.preventDefault();
        toggleFontWheelResizeMode();
      }
    }
    if (e.key === 'Escape' && fontWheelResizeMode) {
      fontWheelResizeMode = false;
      updateFontWheelResizeStatus();
    }
  });

  document.addEventListener('wheel', onFontResizeWheel, { passive: false });
});

// ─── TOAST NOTIFICATIONS ────────────────────────────────
function toast(msg, type = 'info', duration = 3000) {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity 0.3s';
    setTimeout(() => el.remove(), 300);
  }, duration);
}

function updateFontWheelResizeStatus() {
  const right = document.getElementById('status-right');
  if (!right) return;
  if (fontWheelResizeMode && selectedTextEl) {
    right.textContent = 'Resize Mode: Ctrl+T + عجلة الماوس';
  } else {
    right.textContent = '—';
  }
}

function toggleFontWheelResizeMode() {
  if (!selectedTextEl) return;
  fontWheelResizeMode = !fontWheelResizeMode;
  updateFontWheelResizeStatus();
  toast(fontWheelResizeMode ? 'تم تفعيل تكبير الخط بعجلة الماوس' : 'تم إيقاف تكبير الخط بعجلة الماوس', 'info');
}

function setOverlayStrokeStyles(el, strokeEnabled, strokeWidth, strokeColor) {
  el.dataset.strokeEnabled = strokeEnabled ? '1' : '0';
  el.dataset.strokeWidth = String(strokeWidth || 0);
  el.dataset.strokeColor = strokeColor || '#ffffff';

  if (strokeEnabled && strokeWidth > 0) {
    el.style.webkitTextStroke = strokeWidth + 'px ' + strokeColor;
    el.style.textShadow = `0 0 ${strokeWidth}px ${strokeColor}, 0 0 ${strokeWidth}px ${strokeColor}`;
    el.style.paintOrder = 'stroke fill';
  } else {
    el.style.webkitTextStroke = '';
    el.style.textShadow = '';
    el.style.paintOrder = '';
  }
}

function buildStyleSnapshot(t) {
  return {
    font: t.style?.font || 'Hayah',
    font_size: t.style?.font_size || t.font_size_px || 45,
    line_height: t.style?.line_height || t.line_height || 1.1,
    color: t.style?.color || '#000000',
    align: t.style?.align || 'center',
    stroke_enabled: t.style?.stroke_enabled !== false,
    stroke_color: t.style?.stroke_color || '#ffffff',
    stroke_width: t.style?.stroke_width ?? 1,
    rotation: t.style?.rotation || 0,
  };
}

function getTextMeasurer() {
  let el = document.getElementById('text-measurer');
  if (!el) {
    el = document.createElement('div');
    el.id = 'text-measurer';
    el.style.position = 'fixed';
    el.style.left = '-20000px';
    el.style.top = '0';
    el.style.visibility = 'hidden';
    el.style.pointerEvents = 'none';
    el.style.whiteSpace = 'pre-wrap';
    el.style.wordBreak = 'break-word';
    el.style.overflowWrap = 'break-word';
    el.style.boxSizing = 'border-box';
    el.style.padding = '6px';
    document.body.appendChild(el);
  }
  return el;
}

function measureTextFit(text, fontFamily, fontSize, lineHeight, boxWidth) {
  const measurer = getTextMeasurer();
  measurer.style.width = Math.max(20, boxWidth) + 'px';
  measurer.style.fontFamily = fontFamily;
  measurer.style.fontSize = fontSize + 'px';
  measurer.style.lineHeight = String(lineHeight);
  measurer.textContent = text && text.trim() ? text : 'نص';

  const h = measurer.scrollHeight;
  const w = Math.min(measurer.scrollWidth, Math.max(20, boxWidth));
  const estimatedLineCount = Math.max(1, Math.round(h / Math.max(1, fontSize * lineHeight)));
  return { width: w, height: h, lineCount: estimatedLineCount };
}

// Deterministic smart size from box + char count (no DOM needed):
// largest size whose estimated wrapped lines fit the box height.
function estimateSmartSize(text, boxW, boxH, lineHeight) {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  const n = Math.max(1, clean.length);
  const r = hasArabicChars(clean) ? 0.62 : 0.52; // avg advance / font-size
  for (let size = 120; size >= 8; size--) {
    const perLine = Math.max(1, Math.floor((boxW * 0.96) / (size * r)));
    const lines = Math.ceil(n / perLine);
    if (lines * size * lineHeight <= boxH * 0.96) return size;
  }
  return 8;
}

function computeSmartFontSize(t, allowGrow = false) {
  // Site-faithful by default (shrink-only from current size).
  // allowGrow (manual smart button) also grows toward the math best size.
  const text = (t.arabic_text || t.original_text || '').trim();
  const cur = Math.max(8, Math.min(200, parseInt(t.style?.font_size || t.font_size_px || 45, 10) || 45));
  if (!text) return cur;
  const boxWidth = Math.max(80, parseFloat(t.width || 0) || 200);
  const boxHeight = Math.max(40, parseFloat(t.height || 0) || 60);
  const lineHeight = parseFloat(t.style?.line_height || t.line_height || 1.1) || 1.1;
  const font = t.style?.font || 'Hayah';
  const fontFamily = displayFontFor(text, font);
  const fitHeight = boxHeight * 0.96;
  const fitWidth = boxWidth * 0.96;
  const det = estimateSmartSize(text, boxWidth, boxHeight, lineHeight);

  const fits = (size) => {
    const metrics = measureTextFit(text, fontFamily, size, lineHeight, boxWidth);
    return metrics.height <= fitHeight && metrics.width <= fitWidth;
  };

  let size = allowGrow ? Math.min(120, Math.max(cur, det)) : cur;
  if (allowGrow) {
    // grow while it fits (best real size for the bubble)
    while (size < 120) {
      const up = size + 1;
      if (!fits(up)) break;
      size = up;
    }
  }
  while (size > 8 && !fits(size)) size--;
  if (size <= 8 && det >= 14) return det; // DOM measure suspect — trust math
  return Math.max(8, size);
}

// Auto-fit every rendered overlay after load/translation: shrink ONLY
// overflowing TRANSLATED texts so each box "takes its size".
// Untranslated (English) texts are NEVER touched: the source site proves
// they fit at site size. Never enlarges.
function autoFitOverflows() {
  if (!chapterData) return 0;
  let fixed = 0;
  for (const id of Object.keys(textOverlaysById)) {
    const ov = textOverlaysById[id];
    if (!ov || !ov.el || !ov.data) continue;
    const t = ov.data;
    if (!((t.arabic_text || '').trim())) continue;
    const cur = parseInt(t.style?.font_size || t.font_size_px || 45, 10) || 45;
    const fit = computeSmartFontSize(t);
    if (fit < cur) {
      ov.el.style.fontSize = fit + 'px';
      if (!t.style) t.style = {};
      t.style.font_size = fit;
      t.font_size_px = fit;
      fixed++;
    }
  }
  if (fixed > 0) {
    markDirty();
    toast(`ضُبط حجم الخط تلقائياً لـ ${fixed} نصاً (اضغط حفظ)`, 'info');
  }
  return fixed;
}

async function rerenderCurrentView() {
  if (viewMode === 'single') await renderSinglePage(currentPage);
  else await renderWebtoon();
}

async function applyFontSizeToAll() {
  pushUndo();
  const ov = getSelectedOverlay();
  if (!ov) {
    toast('اختر نصاً أولاً', 'warning');
    return;
  }
  const fontSize = parseInt(document.getElementById('prop-size').value, 10) || 45;
  for (const page of chapterData.pages || []) {
    for (const tt of (page.texts || [])) {
      tt.style = tt.style || {};
      tt.style.font_size = fontSize;
      tt.font_size_px = fontSize;
    }
  }
  markDirty();
  await rerenderCurrentView();
  toast(`تم تطبيق حجم ${fontSize}px على كل النصوص`, 'success');
}

async function smartFitTexts(scope = 'all') {
  // Server-side fit = the SAME function the auto worker uses (box growth
  // first, font shrink last). Guarantees manual == automatic results.
  if (!chapterData) return;
  pushUndo();
  toast('ضبط ذكي عبر الخادم...', 'info');
  try {
    const res = await fetch(
      `/api/chapter/${currentSlug}/${currentChapter}/smart-fit`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ grow_boxes: true, unify_stroke: false }) });
    const data = await res.json();
    const res2 = await fetch(`/api/chapter/${currentSlug}/${currentChapter}`);
    chapterData = await res2.json();
    await rerenderCurrentView();
    autoFittedKey = `${currentSlug}::${currentChapter}`;
    toast(`ضبط ذكي: صناديق ${data.grown || 0}، خط ${data.shrunk || 0} (بقي ${data.kept || 0})`, 'success');
  } catch (e) {
    toast('فشل الضبط الذكي: ' + e.message, 'error');
  }
}

function onFontResizeWheel(e) {
  if (!fontWheelResizeMode || !selectedTextEl) return;
  if (!e.ctrlKey) return;
  if (!e.target.closest('.text-overlay') && !e.target.closest('#canvas-container') && !e.target.closest('#properties-panel')) return;

  const ov = getSelectedOverlay();
  if (!ov) return;
  e.preventDefault();

  const currentSize = parseInt(ov.el.style.fontSize, 10) || ov.data.font_size_px || 45;
  const delta = e.deltaY < 0 ? 1 : -1;
  const nextSize = Math.max(6, Math.min(200, currentSize + delta));
  document.getElementById('prop-size').value = nextSize;
  onPropChange();
}

function persistLayoutPrefs() {
  const app = document.getElementById('app');
  if (!app) return;
  const prefs = {
    inspectorCompact: app.classList.contains('inspector-compact'),
    inspectorHidden: app.classList.contains('inspector-hidden'),
    focusWorkspace: app.classList.contains('focus-workspace'),
  };
  try {
    localStorage.setItem(layoutPrefsKey, JSON.stringify(prefs));
  } catch (e) {}
}

function restoreLayoutPrefs() {
  const app = document.getElementById('app');
  if (!app) return;
  try {
    const raw = localStorage.getItem(layoutPrefsKey);
    if (!raw) return;
    const prefs = JSON.parse(raw);
    app.classList.toggle('inspector-compact', !!prefs.inspectorCompact);
    app.classList.toggle('inspector-hidden', !!prefs.inspectorHidden);
    app.classList.toggle('focus-workspace', !!prefs.focusWorkspace);
  } catch (e) {}
}

function toggleInspectorCompact() {
  const app = document.getElementById('app');
  app.classList.toggle('inspector-compact');
  if (app.classList.contains('inspector-hidden')) {
    app.classList.remove('inspector-hidden');
  }
  persistLayoutPrefs();
}

function togglePropertiesPanel() {
  const app = document.getElementById('app');
  app.classList.toggle('inspector-hidden');
  persistLayoutPrefs();
}

function toggleWorkspaceFocus() {
  const app = document.getElementById('app');
  const next = !app.classList.contains('focus-workspace');
  app.classList.toggle('focus-workspace', next);
  if (next) {
    app.classList.add('inspector-hidden');
  }
  persistLayoutPrefs();
}

// ─── CHAPTER SELECTOR ───────────────────────────────────
async function loadChapterList() {
  try {
    const res = await fetch('/api/chapters');
    const data = await res.json();
    const list = document.getElementById('chapter-list');
    if (!data.chapters || data.chapters.length === 0) {
      list.innerHTML = '<div class="loading-chapters">لا توجد فصول بعد. قم بتشغيل السكرابر أولاً.</div>';
      return;
    }
    list.innerHTML = data.chapters.map(ch => `
      <div class="chapter-item">
        <div class="chapter-item-main" onclick="loadChapter('${ch.slug}','${ch.chapter}')">
          <div>
            <div class="chapter-item-title">${ch.title || ch.slug} — فصل ${ch.chapter}</div>
            <div class="chapter-item-meta">${ch.total_pages} صفحة • ${ch.total_texts} نص</div>
          </div>
          <div style="color:var(--accent)">← فتح</div>
        </div>
        <div class="chapter-item-actions">
          <button class="btn-icon" onclick="resetChapter('${ch.slug}','${ch.chapter}',event)" title="مسح الترجمة">⟳</button>
          <button class="btn-icon btn-icon-danger" onclick="deleteChapter('${ch.slug}','${ch.chapter}',event)" title="حذف الفصل">✕</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    document.getElementById('chapter-list').innerHTML =
      '<div class="loading-chapters" style="color:var(--danger)">خطأ في تحميل الفصول</div>';
    console.error(e);
  }
}

function showChapterSelector() {
  document.getElementById('chapter-modal').classList.remove('hidden');
  loadChapterList();
}
function hideChapterSelector() {
  document.getElementById('chapter-modal').classList.add('hidden');
}

async function deleteChapter(slug, chapter, event) {
  event.stopPropagation();
  if (!confirm(`هل أنت متأكد من حذف "${slug} — فصل ${chapter}" بالكامل؟`)) return;
  try {
    const res = await fetch(`/api/chapter/${slug}/${chapter}`, { method: 'DELETE' });
    if (!res.ok) throw new Error((await res.json()).detail || 'خطأ');
    toast(`تم حذف الفصل ${chapter}`, 'success', 3000);
    loadChapterList();
  } catch (e) {
    toast('خطأ في الحذف: ' + e.message, 'error', 4000);
  }
}

async function resetChapter(slug, chapter, event) {
  event.stopPropagation();
  if (!confirm(`مسح كل الترجمة العربية من "${slug} — فصل ${chapter}"؟`)) return;
  try {
    const res = await fetch(`/api/chapter/${slug}/${chapter}/reset`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.json()).detail || 'خطأ');
    const d = await res.json();
    toast(`تم مسح ${d.cleared} نص من الفصل ${chapter}`, 'success', 3000);
    loadChapterList();
  } catch (e) {
    toast('خطأ في المسح: ' + e.message, 'error', 4000);
  }
}

// ─── LOAD CHAPTER ───────────────────────────────────────
async function loadChapter(slug, chapter) {
  if (isDirty) {
    const doSave = confirm('لديك تغييرات غير محفوظة — حفظ قبل المتابعة؟\nموافق = حفظ ومتابعة، إلغاء = اختيار المتابعة بدون حفظ أو الإيقاف');
    if (doSave) {
      await saveAll();
    } else {
      if (!confirm('متابعة بدون حفظ؟ (ستفقد تغييراتك)\nموافق = متابعة، إلغاء = البقاء')) return;
    }
  }
  hideChapterSelector();
  const welcome = document.querySelector('.welcome-overlay');
  if (welcome) welcome.remove();

  currentSlug = slug;
  currentChapter = chapter;
  currentPage = 1;

  try {
    const res = await fetch(`/api/chapter/${slug}/${chapter}`);
    chapterData = await res.json();
    totalPages = chapterData.total_images || chapterData.pages.length;

    document.getElementById('chapter-title-display').textContent =
      `${chapterData.title || slug} — فصل ${chapter}`;
    document.getElementById('prop-page-section').classList.remove('hidden');

    if (viewMode === 'single') await renderSinglePage(1);
    else await renderWebtoon();
    autoFitOnceForChapter();
    toast('تم فتح الفصل بنجاح', 'success');
  } catch (e) {
    toast('خطأ في تحميل الفصل', 'error');
    console.error(e);
  }
}

// ─── CLEAR STACK ────────────────────────────────────────
function clearStack() {
  const stack = document.getElementById('pages-stack');
  if (stack) stack.innerHTML = '';
  pageBlocks = [];
  textOverlaysById = {};
  pageOffsets = [];
}

// ─── RENDER STACK (one HTML block per page) ────────────
async function renderWebtoon() {
  if (!chapterData) return;
  document.getElementById('page-loading').classList.remove('hidden');
  clearStack();

  const pages = chapterData.pages || [];
  const stack = document.getElementById('pages-stack');
  pageOffsets = [];
  let globalY = 0;

  try {
    for (let i = 0; i < pages.length; i++) {
      const page = pages[i];
      const pageNum = page.page;
      const w = page.width || 800;
      const h = page.height || 1600;

      pageOffsets.push(globalY);

      const block = document.createElement('div');
      block.className = 'page-block';
      block.dataset.page = pageNum;
      block.style.width = w + 'px';
      block.style.height = h + 'px';

      const label = document.createElement('div');
      label.className = 'page-label';
      label.textContent = `صفحة ${pageNum}`;
      block.appendChild(label);

      const img = document.createElement('img');
      img.src = `/api/chapter/${currentSlug}/${currentChapter}/page/${pageNum}`;
      img.alt = `Page ${pageNum}`;
      img.draggable = false;
      block.appendChild(img);

      const texts = page.texts || [];
      for (const t of texts) {
        const overlay = createTextOverlay(t, block);
        block.appendChild(overlay);
      }

      // Observe visibility for current page tracking
      observeBlockVisibility(block, pageNum);

      stack.appendChild(block);
      pageBlocks.push(block);
      globalY += h + 16;
    }

    currentPage = 1;
    totalPages = pages.length;
    document.getElementById('page-indicator').textContent =
      `1 / ${totalPages} (${pages.length} صفحة)`;
    document.getElementById('canvas-container').scrollTop = 0;
    hidePageLoading();
    onSelectionCleared();
  } catch (e) {
    console.error('Render error:', e);
    hidePageLoading();
    toast('خطأ في تحميل الصفحات', 'error');
  }
}

function hidePageLoading() {
  document.getElementById('page-loading').classList.add('hidden');
}

// ─── RENDER SINGLE PAGE ─────────────────────────────────
async function renderSinglePage(pageNum) {
  if (!chapterData) return;
  document.getElementById('page-loading').classList.remove('hidden');
  clearStack();

  const pageData = chapterData.pages.find(p => p.page === pageNum);
  if (!pageData) {
    hidePageLoading();
    return;
  }

  const w = pageData.width || 800;
  const h = pageData.height || 1600;

  const stack = document.getElementById('pages-stack');
  const block = document.createElement('div');
  block.className = 'page-block';
  block.style.width = w + 'px';
  block.style.height = h + 'px';

  const img = document.createElement('img');
  img.src = `/api/chapter/${currentSlug}/${currentChapter}/page/${pageNum}`;
  block.appendChild(img);

  for (const t of (pageData.texts || [])) {
    const overlay = createTextOverlay(t, block);
    block.appendChild(overlay);
  }

  stack.appendChild(block);
  pageBlocks.push(block);
  pageOffsets = [0];
  currentPage = pageNum;
  document.getElementById('page-indicator').textContent =
    `${currentPage} / ${totalPages}`;

  hidePageLoading();
  onSelectionCleared();
}

// Auto-fit runs ONCE per opened chapter (never on every render, so manual
// sizes and "apply to all" are never crushed back).
let autoFittedKey = '';
function autoFitOnceForChapter() {
  if (!isAutoModeEnabled()) return;
  const key = `${currentSlug}::${currentChapter}`;
  if (autoFittedKey === key) return;
  autoFittedKey = key;
  autoFitOverflows();
}

// ─── VISIBILITY OBSERVER ────────────────────────────────
const observerState = { current: null };
function setupIntersectionObserver() {
  if (window._pageObserver) window._pageObserver.disconnect();
  window._pageObserver = new IntersectionObserver((entries) => {
    let best = null, bestRatio = 0;
    for (const e of entries) {
      if (e.isIntersecting && e.intersectionRatio > bestRatio) {
        best = e; bestRatio = e.intersectionRatio;
      }
    }
    if (best && best.target.dataset.page) {
      currentPage = parseInt(best.target.dataset.page);
      document.getElementById('page-indicator').textContent =
        `${currentPage} / ${totalPages}`;
    }
  }, { threshold: [0.1, 0.3, 0.5, 0.7], rootMargin: '-100px 0px -100px 0px' });
}
function observeBlockVisibility(block, pageNum) {
  if (!window._pageObserver) setupIntersectionObserver();
  window._pageObserver.observe(block);
}

// ─── CREATE TEXT OVERLAY (DOM-based) ────────────────────
function createTextOverlay(t, parentBlock) {
  const style = t.style || {};
  const fontSize = style.font_size || t.font_size_px || 45;
  const lineHeight = parseFloat(style.line_height || t.line_height || 1.1);
  const textColor = rgbToHex(style.color || '#000000') || '#000000';
  const fontFamily = style.font || 'Hayah';
  const align = style.align || 'center';
  const strokeEnabled = style.stroke_enabled !== false;
  const strokeWidth = parseFloat(style.stroke_width || 0.5);
  const strokeColor = rgbToHex(style.stroke_color || '#ffffff') || '#ffffff';
  const rotation = parseFloat(style.rotation != null ? style.rotation : (t.rotation || 0));

  const el = document.createElement('div');
  el.className = 'text-overlay';
  el.dataset.textId = t.id;
  el.dataset.page = t.page || '';
  el.dataset.baseFont = fontFamily;
  el.style.left = (t.x || 0) + 'px';
  el.style.top = (t.y || 0) + 'px';
  el.style.width = (t.width || 200) + 'px';
  el.style.height = (t.height || 60) + 'px';
  el.style.fontSize = fontSize + 'px';
  el.style.fontFamily = displayFontFor(t.arabic_text || t.original_text || '', fontFamily);
  el.style.color = textColor;
  el.style.justifyContent = align === 'left' ? 'flex-start' : (align === 'right' ? 'flex-end' : 'center');
  el.style.textAlign = align;
  setOverlayStrokeStyles(el, strokeEnabled, strokeWidth, strokeColor);
  el.style.padding = '6px';
  el.style.boxSizing = 'border-box';
  el.style.lineHeight = String(lineHeight);
  el.style.wordBreak = 'break-word';
  el.style.overflowWrap = 'break-word';
  el.style.userSelect = currentTool === 'select' ? 'none' : 'text';
  el.style.touchAction = 'none';
  el.style.whiteSpace = 'pre-wrap';
  el.style.transformOrigin = 'center center';
  el.style.transform = `rotate(${rotation}deg)`;
  el.textContent = t.arabic_text || '';
  if (t.approved === 'approved') el.classList.add('approved');
  if (t.approved === 'needs_fix') el.classList.add('needs-fix');

  parentBlock.appendChild(el);
  textOverlaysById[t.id] = { el, data: t };

  // Click to select (review mode: second click starts inline edit)
  el.addEventListener('click', (e) => {
    if (currentTool === 'pan') return;
    if (e.target.closest('.handle')) return;
    e.stopPropagation();
    const wasSelected = selectedTextEl === el;
    selectText(t.id, el);
    if (editorMode === 'review' && wasSelected && !isViewOnly()) {
      startInlineEdit(el, t);
    }
  });

  // Drag functionality
  makeDraggable(el, t);

  // Double-click to edit inline
  el.addEventListener('dblclick', (e) => {
    e.stopPropagation();
    startInlineEdit(el, t);
  });

  return el;
}

function makeDraggable(el, t) {
  let startX, startY, initialLeft, initialTop, isDragging = false;
  el.addEventListener('mousedown', (e) => {
    if (isViewOnly()) return;
    if (e.button !== 0) return;
    if (currentTool === 'pan') return;
    if (e.target.closest('.handle')) return; // Let resize handles handle this
    if (el.getAttribute('contenteditable') === 'true') return;
    e.preventDefault();
    e.stopPropagation();
    isDragging = true;
    startX = e.clientX;
    startY = e.clientY;
    initialLeft = parseInt(el.style.left);
    initialTop = parseInt(el.style.top);
    selectText(t.id, el);
  });
  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    const zoom = currentZoom || 1;
    const dx = (e.clientX - startX) / zoom;
    const dy = (e.clientY - startY) / zoom;
    el.style.left = (initialLeft + dx) + 'px';
    el.style.top = (initialTop + dy) + 'px';
    // Live update properties
    const overlay = textOverlaysById[t.id];
    if (overlay) overlay.data.x = Math.round(initialLeft + dx);
    if (overlay) overlay.data.y = Math.round(initialTop + dy);
  });
  document.addEventListener('mouseup', () => {
    if (isDragging) {
      isDragging = false;
      syncOverlayToData(t.id);
      markDirty();
    }
  });
}

function startInlineEdit(el, t) {
  if (isViewOnly()) { toast('وضع المشاهدة: القراءة فقط', 'warning'); return; }
  if (el.getAttribute('contenteditable') === 'true') return;
  el.setAttribute('contenteditable', 'true');
  el.style.userSelect = 'text';
  el.style.cursor = 'text';
  el.focus();
  // Save on blur or Enter
  const onBlur = () => {
    el.removeAttribute('contenteditable');
    el.style.userSelect = currentTool === 'select' ? 'none' : 'text';
    el.style.cursor = 'move';
    t.arabic_text = el.textContent || '';
    syncOverlayToData(t.id);
    markDirty();
    el.removeEventListener('blur', onBlur);
  };
  el.addEventListener('blur', onBlur);
}

// ─── SCROLL TO PAGE ─────────────────────────────────────
function scrollToPage(pageNum) {
  if (!pageBlocks[pageNum - 1]) return;
  // Use instant scroll for now
  const block = pageBlocks[pageNum - 1];
  const container = document.getElementById('canvas-container');
  const blockTop = block.offsetTop;
  container.scrollTo({ top: blockTop, behavior: 'smooth' });
  currentPage = pageNum;
  document.getElementById('page-indicator').textContent =
    `${currentPage} / ${totalPages}`;
}

function prevPage() {
  const p = Math.max(1, currentPage - 1);
  if (viewMode === 'single') renderSinglePage(p);
  else scrollToPage(p);
}
function nextPage() {
  const p = Math.min(totalPages, currentPage + 1);
  if (viewMode === 'single') renderSinglePage(p);
  else scrollToPage(p);
}
function jumpToPage(num) {
  num = parseInt(num);
  if (isNaN(num) || num < 1 || num > totalPages) return;
  if (viewMode === 'single') renderSinglePage(num);
  else scrollToPage(num);
}

// ─── VIEW MODE TOGGLE ───────────────────────────────────
function toggleViewMode() {
  if (!chapterData) return;
  if (viewMode === 'webtoon') {
    viewMode = 'single';
    renderSinglePage(currentPage);
  } else {
    viewMode = 'webtoon';
    renderWebtoon();
  }
  const btn = document.getElementById('view-mode-btn');
  btn.textContent = viewMode === 'webtoon' ? 'صفحة واحدة' : 'Webtoon';
  btn.title = viewMode === 'webtoon' ? 'تبديل إلى عرض صفحة واحدة' : 'تبديل إلى عرض Webtoon';
}

// ─── RESIZE HANDLES ─────────────────────────────────────
function addResizeHandles(el) {
  removeResizeHandles(el);
  const positions = ['nw','n','ne','w','e','sw','s','se'];
  const handles = {};
  for (const pos of positions) {
    const h = document.createElement('div');
    h.className = `handle handle-${pos}`;
    h.dataset.pos = pos;
    el.appendChild(h);
    handles[pos] = h;
  }
  // Rotation handle: protrudes above the top-center, far enough to avoid grip overlap
  const rot = document.createElement('div');
  rot.className = 'handle handle-rotate';
  rot.dataset.pos = 'rotate';
  rot.title = 'تدوير';
  el.appendChild(rot);
  handles['rotate'] = rot;
  el._resizeHandles = handles;
  el._resizeActive = false;
  el._rotating = false;

  // Mousedown on any handle starts resize or rotate
  el.addEventListener('mousedown', el._resizeDown = (e) => {
    const h = e.target.closest('.handle');
    if (!h) return;
    const pos = h.dataset.pos;
    if (pos === 'rotate') {
      e.preventDefault();
      e.stopPropagation();
      pushUndo();
      el._rotating = true;
      el._rotateStart = currentRotationDegrees(el);
      const rect = el.getBoundingClientRect();
      el._rotateCx = rect.left + rect.width / 2;
      el._rotateCy = rect.top + rect.height / 2;
      el._rotateStartAngle = Math.atan2(e.clientY - el._rotateCy, e.clientX - el._rotateCx) * 180 / Math.PI;
      el.classList.add('rotating');
      return;
    }
    pushUndo();
    e.preventDefault();
    e.stopPropagation();
    el._resizeActive = true;
    el._resizePos = pos;
    el._resizeStartX = e.clientX;
    el._resizeStartY = e.clientY;
    el._resizeStartLeft = parseInt(el.style.left) || 0;
    el._resizeStartTop = parseInt(el.style.top) || 0;
    el._resizeStartW = parseInt(el.style.width) || 200;
    el._resizeStartH = parseInt(el.style.height) || 60;
    el.classList.add('resizing');
  });
}

function removeResizeHandles(el) {
  if (el._resizeHandles) {
    for (const h of Object.values(el._resizeHandles)) h.remove();
    delete el._resizeHandles;
  }
  el.classList.remove('resizing', 'rotating');
  if (el._resizeDown) {
    el.removeEventListener('mousedown', el._resizeDown);
    delete el._resizeDown;
  }
}

function currentRotationDegrees(el) {
  const t = el.style.transform || '';
  const m = t.match(/rotate\(([-\d.]+)deg\)/);
  return m ? parseFloat(m[1]) : 0;
}

// Global mousemove/mouseup for resize + rotate
document.addEventListener('mousemove', (e) => {
  for (const [id, ov] of Object.entries(textOverlaysById)) {
    const el = ov.el, t = ov.data;
    if (!el._resizeActive && !el._rotating) continue;

    if (el._rotating) {
      const cur = Math.atan2(e.clientY - el._rotateCy, e.clientX - el._rotateCx) * 180 / Math.PI;
      let delta = cur - el._rotateStartAngle;
      delta = ((delta + 540) % 360) - 180;
      let newDeg = el._rotateStart + delta;
      newDeg = Math.max(-180, Math.min(180, newDeg));
      el.style.transform = `rotate(${newDeg}deg)`;
      t.style = t.style || {};
      t.style.rotation = newDeg;
      // Live-update the panel input
      const rotInput = document.getElementById('prop-rotation');
      const rotVal = document.getElementById('prop-rotation-val');
      if (rotInput && document.activeElement !== rotInput) {
        rotInput.value = Math.round(newDeg);
        if (rotVal) rotVal.textContent = Math.round(newDeg) + '°';
      }
    }
    if (el._resizeActive) {
      const dx = e.clientX - el._resizeStartX;
      const dy = e.clientY - el._resizeStartY;
      let l = el._resizeStartLeft, t_ = el._resizeStartTop;
      let w = el._resizeStartW, h = el._resizeStartH;
      const pos = el._resizePos;
      const min = 30;

      if (pos.includes('e')) w = Math.max(min, el._resizeStartW + dx);
      if (pos.includes('w')) { w = Math.max(min, el._resizeStartW - dx); l = el._resizeStartLeft + (el._resizeStartW - w); }
      if (pos.includes('s')) h = Math.max(min, el._resizeStartH + dy);
      if (pos.includes('n')) { h = Math.max(min, el._resizeStartH - dy); t_ = el._resizeStartTop + (el._resizeStartH - h); }

      el.style.left = l + 'px';
      el.style.top = t_ + 'px';
      el.style.width = w + 'px';
      el.style.height = h + 'px';
      t.x = l; t.y = t_; t.width = w; t.height = h;
    }
  }
});

document.addEventListener('mouseup', (e) => {
  for (const [id, ov] of Object.entries(textOverlaysById)) {
    const el = ov.el, t = ov.data;
    if (!el._resizeActive && !el._rotating) continue;
    el._resizeActive = false;
    el._rotating = false;
    el.classList.remove('resizing', 'rotating');
    syncOverlayToData(t.id);
    markDirty();
  }
});

// ─── SELECTION ──────────────────────────────────────────
function selectText(textId, el) {
  if (selectedTextEl) selectedTextEl.classList.remove('selected');
  selectedTextEl = el;
  el.classList.add('selected');
  addResizeHandles(el);
  showTextProperties(textId);
  updateFontWheelResizeStatus();
}

function onSelectionCleared() {
  if (selectedTextEl) {
    removeResizeHandles(selectedTextEl);
    selectedTextEl.classList.remove('selected');
    selectedTextEl = null;
  }
  document.getElementById('prop-text').classList.add('hidden');
  document.getElementById('prop-none').classList.remove('hidden');
  if (fontWheelResizeMode) {
    fontWheelResizeMode = false;
    updateFontWheelResizeStatus();
  }
}

// Click on canvas container background to deselect
document.addEventListener('click', (e) => {
  if (!e.target.closest('.text-overlay') && !e.target.closest('.page-block') &&
      !e.target.closest('.panel-section') && !e.target.closest('button') &&
      !e.target.closest('input') && !e.target.closest('textarea') &&
      !e.target.closest('select')) {
    onSelectionCleared();
  }
});

// ─── PROPERTIES PANEL ───────────────────────────────────
function showTextProperties(textId) {
  const overlay = textOverlaysById[textId];
  if (!overlay) return;
  const t = overlay.data;
  const style = t.style || {};

  document.getElementById('prop-none').classList.add('hidden');
  document.getElementById('prop-text').classList.remove('hidden');

  document.getElementById('prop-content').value = overlay.el.textContent || '';
  const origEl = document.getElementById('prop-original');
  if (origEl) origEl.textContent = t.original_text || '(لا يوجد نص أصلي)';
  document.getElementById('prop-font').value = style.font || 'Hayah';
  document.getElementById('prop-size').value = parseInt(overlay.el.style.fontSize) || style.font_size || 45;
  document.getElementById('prop-size-val').textContent = document.getElementById('prop-size').value;
  const lineHeight = parseFloat(style.line_height || t.line_height || overlay.el.style.lineHeight || 1.1);
  document.getElementById('prop-line-height').value = lineHeight;
  document.getElementById('prop-line-height-val').textContent = lineHeight.toFixed(2);

  const textColor = style.color && style.color.startsWith('#') ? style.color : rgbToHex(overlay.el.style.color);
  document.getElementById('prop-color').value = textColor;
  document.getElementById('prop-color-hex').value = textColor;

  const align = style.align || 'center';
  document.querySelectorAll('.align-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.align === align);
  });

  const strokeEnabled = style.stroke_enabled !== false && style.stroke_enabled !== undefined;
  const strokeWidth = parseFloat(style.stroke_width || 0.5);
  const strokeColor = (style.stroke_color && style.stroke_color.startsWith('#')) ? style.stroke_color : '#ffffff';
  document.getElementById('prop-stroke-enable').checked = strokeEnabled;
  document.getElementById('stroke-options').classList.toggle('hidden', !strokeEnabled);
  document.getElementById('prop-stroke-color').value = strokeColor;
  document.getElementById('prop-stroke-hex').value = strokeColor;
  document.getElementById('prop-stroke-width').value = strokeWidth;
  document.getElementById('prop-stroke-width-val').textContent = strokeWidth;

  document.getElementById('prop-x').value = parseInt(overlay.el.style.left) || 0;
  document.getElementById('prop-y').value = parseInt(overlay.el.style.top) || 0;
  document.getElementById('prop-width').value = parseInt(overlay.el.style.width) || 0;
  document.getElementById('prop-height').value = parseInt(overlay.el.style.height) || 0;

  const rotationVal = Math.round(parseFloat(style.rotation != null ? style.rotation : 0));
  const rotInput = document.getElementById('prop-rotation');
  const rotValLabel = document.getElementById('prop-rotation-val');
  if (rotInput) rotInput.value = rotationVal;
  if (rotValLabel) rotValLabel.textContent = rotationVal + '°';
  updateReviewLabel(t);
}

function getSelectedOverlay() {
  if (!selectedTextEl) return null;
  const id = selectedTextEl.dataset.textId;
  return textOverlaysById[id] || null;
}

function onPropChange() {
  if (isViewOnly()) { toast('وضع المشاهدة: القراءة فقط', 'warning'); return; }
  const ov = getSelectedOverlay();
  if (!ov) return;
  const el = ov.el;
  const t = ov.data;

  const text = document.getElementById('prop-content').value;
  el.textContent = text;
  t.arabic_text = text;

  const fontSize = parseInt(document.getElementById('prop-size').value);
  el.style.fontSize = fontSize + 'px';
  const lineHeight = parseFloat(document.getElementById('prop-line-height').value || '1.1');
  el.style.lineHeight = String(lineHeight);

  const rawColor = document.getElementById('prop-color').value;
  el.style.color = rawColor;

  const font = document.getElementById('prop-font').value;
  el.dataset.baseFont = font;
  el.style.fontFamily = displayFontFor(el.textContent || text, font);

  const align = document.querySelector('.align-btn.active')?.dataset?.align || 'center';
  el.style.justifyContent = align === 'left' ? 'flex-start' : (align === 'right' ? 'flex-end' : 'center');
  el.style.textAlign = align;

  const strokeEnabled = document.getElementById('prop-stroke-enable').checked;
  const strokeColor = document.getElementById('prop-stroke-color').value;
  const strokeWidth = parseFloat(document.getElementById('prop-stroke-width').value);

  setOverlayStrokeStyles(el, strokeEnabled, strokeWidth, strokeColor);

  document.getElementById('prop-size-val').textContent = fontSize;
  document.getElementById('prop-line-height-val').textContent = lineHeight.toFixed(2);
  document.getElementById('prop-stroke-width-val').textContent = strokeWidth;
  const displayColor = rgbToHex(el.style.color) || rawColor;
  if (document.activeElement !== document.getElementById('prop-color-hex')) {
    document.getElementById('prop-color-hex').value = displayColor;
  }
  if (document.activeElement !== document.getElementById('prop-stroke-hex')) {
    document.getElementById('prop-stroke-hex').value = strokeColor;
  }
  document.getElementById('stroke-options').classList.toggle('hidden', !strokeEnabled);

  const x = parseFloat(document.getElementById('prop-x').value);
  const y = parseFloat(document.getElementById('prop-y').value);
  const w = parseFloat(document.getElementById('prop-width').value);
  const h = parseFloat(document.getElementById('prop-height').value);
  if (!isNaN(x) && x > -1) { el.style.left = Math.round(x) + 'px'; t.x = Math.round(x); }
  if (!isNaN(y) && y > -1) { el.style.top = Math.round(y) + 'px'; t.y = Math.round(y); }
  if (!isNaN(w) && w > 10) { el.style.width = Math.round(w) + 'px'; t.width = Math.round(w); }
  if (!isNaN(h) && h > 10) { el.style.height = Math.round(h) + 'px'; t.height = Math.round(h); }

  const rotationRaw = document.getElementById('prop-rotation');
  let rotation = 0;
  if (rotationRaw) {
    rotation = Math.max(-180, Math.min(180, parseFloat(rotationRaw.value) || 0));
    el.style.transform = `rotate(${rotation}deg)`;
    const rotValLabel = document.getElementById('prop-rotation-val');
    if (rotValLabel) rotValLabel.textContent = Math.round(rotation) + '°';
  }

  if (!t.style) t.style = {};
  t.style.font = font;
  t.style.font_size = fontSize;
  t.style.line_height = lineHeight;
  t.font_size_px = fontSize;
  t.line_height = lineHeight;
  t.style.color = rgbToHex(el.style.color) || rawColor;
  t.style.stroke_enabled = strokeEnabled;
  t.style.stroke_color = strokeColor;
  t.style.stroke_width = strokeEnabled ? strokeWidth : 0;
  t.style.align = align;
  t.style.rotation = rotation;

  markDirty();
}

function setAlign(align) {
  document.querySelectorAll('.align-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.align === align);
  });
  onPropChange();
}

function syncOverlayToData(textId) {
  const ov = textOverlaysById[textId];
  if (!ov || !chapterData) return;
  const el = ov.el;
  const t = ov.data;
  t.arabic_text = el.textContent || '';
  t.x = parseInt(el.style.left) || 0;
  t.y = parseInt(el.style.top) || 0;
  t.width = parseInt(el.style.width) || 0;
  t.height = parseInt(el.style.height) || 0;
  t.font_size_px = parseInt(el.style.fontSize) || 45;
  t.line_height = parseFloat(el.style.lineHeight) || 1.1;
  if (!t.style) t.style = {};
  t.style.font = el.dataset.baseFont || el.style.fontFamily.replace(/"/g, '').split(',')[0].trim();
  t.style.font_size = t.font_size_px;
  t.style.line_height = t.line_height;
  t.style.color = rgbToHex(el.style.color) || '#000000';
  t.style.align = el.style.textAlign || 'center';
  t.style.rotation = currentRotationDegrees(el);
  t.style.stroke_enabled = el.dataset.strokeEnabled !== '0';
  t.style.stroke_width = parseFloat(el.dataset.strokeWidth || t.style.stroke_width || 1) || 1;
  t.style.stroke_color = (el.dataset.strokeColor || t.style.stroke_color || '#ffffff').toLowerCase();
}

// ─── SAVE ───────────────────────────────────────────────
function markDirty() {
  isDirty = true;
  document.getElementById('status-left').textContent = 'غير محفوظ';
  if (saveTimeout) clearTimeout(saveTimeout);
  saveTimeout = setTimeout(saveAll, 1500);
}

async function saveAll() {
  if (!chapterData) return false;
  if (isViewOnly()) { toast('وضع المشاهدة: القراءة فقط — لن يتم الحفظ', 'warning'); return false; }
  if (!isDirty) return true;
  if (savePromise) return savePromise;

  savePromise = (async () => {
    isSaving = true;
    document.getElementById('status-left').textContent = 'جاري الحفظ...';

    try {
      for (const textId in textOverlaysById) {
        syncOverlayToData(textId);
      }

      const res = await fetch(`/api/chapter/${currentSlug}/${currentChapter}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pages: chapterData.pages }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== 'ok') {
        throw new Error(data.detail || data.message || 'فشل الحفظ');
      }

      isDirty = false;
      document.getElementById('status-left').textContent = 'تم الحفظ';
      toast('تم حفظ التغييرات', 'success');
      return true;
    } catch (e) {
      console.error('Save error:', e);
      document.getElementById('status-left').textContent = 'فشل الحفظ';
      toast('خطأ في الحفظ: ' + e.message, 'error');
      return false;
    } finally {
      isSaving = false;
      savePromise = null;
    }
  })();

  return savePromise;
}

async function deleteTextObject() {
  if (guardViewOnly()) return;
  pushUndo();
  const ov = getSelectedOverlay();
  if (!ov) return;
  const t = ov.data;
  const id = t.id;
  ov.el.remove();
  delete textOverlaysById[id];
  if (chapterData) {
    for (const page of chapterData.pages) {
      page.texts = page.texts.filter(tt => tt.id !== id);
    }
  }
  selectedTextEl = null;
  onSelectionCleared();
  markDirty();
  toast('تم حذف النص', 'info');
}

// ─── BATCH STYLE ────────────────────────────────────────
async function applyStyleToAll() {
  if (guardViewOnly()) return;
  pushUndo();
  const ov = getSelectedOverlay();
  if (!ov) {
    toast('اختر نصاً أولاً', 'warning');
    return;
  }
  const t = ov.data;

  const style = buildStyleSnapshot(t);

  for (const page of chapterData.pages || []) {
    for (const tt of (page.texts || [])) {
      tt.style = { ...(tt.style || {}), ...style };
      tt.font_size_px = style.font_size;
      tt.line_height = style.line_height;
    }
  }

  try {
    await fetch(`/api/chapter/${currentSlug}/${currentChapter}/batch-style`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ style, scope: 'all' }),
    });
    toast('تم تطبيق التنسيق على كل النصوص', 'success');
    if (viewMode === 'single') await renderSinglePage(currentPage);
    else await renderWebtoon();
  } catch (e) {
    toast('خطأ في تطبيق التنسيق', 'error');
  }
}

async function applyStyleToPage() {
  if (guardViewOnly()) return;
  pushUndo();
  const ov = getSelectedOverlay();
  if (!ov) {
    toast('اختر نصاً أولاً', 'warning');
    return;
  }
  const t = ov.data;

  const style = buildStyleSnapshot(t);

    const pageData = chapterData.pages.find(p => p.page === currentPage);
  if (pageData) {
    for (const tt of pageData.texts) {
      tt.style = { ...tt.style, ...style };
      tt.font_size_px = style.font_size;
      tt.line_height = style.line_height;
    }
    markDirty();
  }
  toast('تم تطبيق التنسيق على الصفحة', 'success');
}

// ─── TOOLS ──────────────────────────────────────────────
function setTool(tool) {
  currentTool = tool;
  document.querySelectorAll('#left-tool-dock .dock-btn[data-tool]').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById(`tool-${tool}`);
  if (btn) btn.classList.add('active');
}

// ─── ZOOM (CSS transform on the canvas-wrapper) ─────────
function getCurrentZoom() { return currentZoom || 1; }
function setZoom(level) {
  level = typeof level === 'string' ? parseFloat(level) : level;
  level = Math.max(0.5, Math.min(2, level));
  currentZoom = level;
  const wrapper = document.getElementById('canvas-wrapper');
  wrapper.style.transform = `scale(${level})`;
  wrapper.style.transformOrigin = 'top center';
  const zoomSel = document.getElementById('zoom-select');
  if (zoomSel) zoomSel.value = level;
}

function zoomIn() {
  setZoom(getCurrentZoom() + 0.1);
}
function zoomOut() {
  setZoom(getCurrentZoom() - 0.1);
}
function zoomFit() {
  const container = document.getElementById('canvas-container');
  const cw = container.clientWidth - 40;
  if (!pageBlocks.length) return;
  const block = pageBlocks[0];
  const w = block.offsetWidth || 800;
  const fit = Math.min(cw / w, 1);
  setZoom(fit);
}

// ─── EXPORT ─────────────────────────────────────────────
async function exportAll() {
  const saved = await saveAll();
  if (!saved) return;
  try {
    const res = await fetch(`/api/chapter/${currentSlug}/${currentChapter}/export`, {
      method: 'POST',
    });
    const data = await res.json();
    const files = data.files || [];
    toast(`تم تصدير ${files.length} ملف`, 'success');
    if (files.length > 0) {
      window.open(`/api/chapter/${currentSlug}/${currentChapter}/exported/${encodeURIComponent(files[0])}`, '_blank');
    }
  } catch (e) {
    toast('خطأ في التصدير', 'error');
  }
}

async function downloadZip() {
  const saved = await saveAll();
  if (!saved) return;
  try {
    const res = await fetch(`/api/chapter/${currentSlug}/${currentChapter}/export`, {
      method: 'POST',
    });
    const data = await res.json();
    if (data.files && data.files.length > 0) {
      window.location.href = `/api/chapter/${currentSlug}/${currentChapter}/download-zip`;
      toast('جاري تحميل الملف...', 'info');
    } else {
      toast('لا توجد صور للتحميل', 'warning');
    }
  } catch (e) {
    toast('خطأ في التحميل', 'error');
  }
}



// ─── ADD NEW TEXT ───────────────────────────────────────
function addNewText() {
  if (guardViewOnly()) return;
  if (!chapterData) {
    toast('افتح فصلاً أولاً', 'warning');
    return;
  }
  pushUndo();
  const pageData = chapterData.pages.find(p => p.page === currentPage);
  if (!pageData) return;

  // Make sure page is rendered in single mode for adding
  if (viewMode === 'webtoon') {
    // Find the block for current page; add text to its DOM
    let block = pageBlocks.find(b => parseInt(b.dataset.page) === currentPage);
    if (!block) return;
  }

  const newId = `text-new-${Date.now()}`;
  const newText = {
    id: newId,
    page: currentPage,
    original_text: '',
    arabic_text: 'نص جديد',
    x: 100,
    y: 100,
    width: 400,
    height: 80,
    font_size_px: 45,
    line_height: 1.1,
    scale_factor: 1.0,
    style: {
      font: 'Hayah',
      font_size: 45,
      line_height: 1.1,
      color: '#000000',
      stroke_color: '#FFFFFF',
      stroke_width: 1,
      stroke_enabled: true,
      align: 'center',
      rotation: 0,
    },
  };

  pageData.texts.push(newText);
  let block;
  if (viewMode === 'single') {
    block = pageBlocks[0];
  } else {
    block = pageBlocks.find(b => parseInt(b.dataset.page) === currentPage);
  }
  if (block) {
    const overlay = createTextOverlay(newText, block);
    selectText(newId, overlay);
  }
  markDirty();
  toast('تم إضافة نص جديد', 'success');
}

function deleteSelected() {
  const ov = getSelectedOverlay();
  if (ov) {
    deleteTextObject();
  } else {
    toast('اختر نصاً لحذفه', 'warning');
  }
}

function showFontUpload() {
  document.getElementById('font-modal').classList.remove('hidden');
  document.getElementById('font-file-input').value = '';
  document.getElementById('font-upload-status').classList.add('hidden');
}

function hideFontUpload() {
  document.getElementById('font-modal').classList.add('hidden');
}

// ─── FONT MANAGEMENT ────────────────────────────────────
function injectFontFaces(fonts) {
  const existing = document.getElementById('dynamic-font-faces');
  if (existing) existing.remove();
  const style = document.createElement('style');
  style.id = 'dynamic-font-faces';
  let css = '';
  fonts.forEach(f => {
    const ext = f.filename.split('.').pop().toLowerCase();
    const format = ext === 'otf' ? 'opentype' : ext === 'ttc' ? 'truetype-collection' : 'truetype';
    css += `@font-face{font-family:'${f.name}';src:url('/fonts/${f.url}') format('${format}');font-weight:normal;font-style:normal;}`;
  });
  style.textContent = css;
  document.head.appendChild(style);
}

async function loadFonts() {
  try {
    const res = await fetch('/api/fonts');
    const data = await res.json();
    const select = document.getElementById('prop-font');
    const currentVal = select.value;
    select.innerHTML = '';
    (data.fonts || []).forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.name;
      opt.textContent = f.name + (f.user_uploaded ? ' [مرفوع]' : '');
      select.appendChild(opt);
    });
    if (currentVal && Array.from(select.options).some(o => o.value === currentVal)) {
      select.value = currentVal;
    }
    injectFontFaces(data.fonts || []);
  } catch (e) {
    console.error('Font load error:', e);
  }
}

async function uploadFont(input) {
  const file = input.files[0];
  if (!file) return;

  const status = document.getElementById('font-upload-status');
  status.classList.remove('hidden');
  status.textContent = 'جاري الرفع...';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/fonts/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.status === 'ok') {
      status.innerHTML = `<span style="color:var(--success)">✓ تم رفع الخط: ${data.font}</span>`;
      loadFonts();
    } else {
      status.innerHTML = `<span style="color:var(--danger)">✗ خطأ: ${JSON.stringify(data)}</span>`;
    }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--danger)">✗ فشل الرفع</span>`;
  }

  setTimeout(() => { status.classList.add('hidden'); }, 3000);
}

// ─── SCRAPE MODAL ───────────────────────────────────────
function showScrapeModal() {
  document.getElementById('scrape-modal').classList.remove('hidden');
  document.getElementById('scrape-url').value = '';
  document.getElementById('scrape-status').classList.add('hidden');
  const bs = document.getElementById('batch-status');
  if (bs) { bs.classList.add('hidden'); bs.textContent = ''; }
  loadScrapeProviders();
  try { loadAutoModePrefs(); } catch (e) {}
}
function hideScrapeModal() {
  document.getElementById('scrape-modal').classList.add('hidden');
}

async function loadScrapeProviders() {
  const sel = document.getElementById('scrape-provider');
  const modelSel = document.getElementById('scrape-provider-model');
  if (!sel || !modelSel) return;
  try {
    const res = await fetch('/api/providers');
    const providers = await res.json();
    sel.innerHTML = '';
    const ids = Object.keys(providers).filter(k => k !== 'custom');
    for (const pid of ids) {
      const opt = document.createElement('option');
      opt.value = pid;
      opt.textContent = providers[pid].name || pid;
      sel.appendChild(opt);
    }
    if (selectedProvider && ids.includes(selectedProvider)) sel.value = selectedProvider;
    const updateModels = () => {
      const pid = sel.value;
      const p = providers[pid];
      modelSel.innerHTML = '';
      for (const m of (p && p.models) || []) {
        const o = document.createElement('option');
        o.value = m.id;
        o.textContent = m.name || m.id;
        modelSel.appendChild(o);
      }
      if (selectedProvider === pid && selectedProviderModel) {
        const exists = Array.from(modelSel.options).some(o => o.value === selectedProviderModel);
        if (exists) modelSel.value = selectedProviderModel;
      } else if (p && p.default_model) {
        const exists = Array.from(modelSel.options).some(o => o.value === p.default_model);
        if (exists) modelSel.value = p.default_model;
      }
    };
    sel.onchange = updateModels;
    updateModels();
  } catch (e) {
    console.error('loadScrapeProviders error:', e);
  }
}

function getScrapeAutoTranslate() {
  const checked = document.getElementById('scrape-translate')
    ? document.getElementById('scrape-translate').checked : false;
  if (!checked) return null;
  const pid = document.getElementById('scrape-provider')?.value || selectedProvider;
  const model = document.getElementById('scrape-provider-model')?.value || selectedProviderModel;
  if (!pid || !model) return null;
  return { provider_id: pid, model };
}

// ─── AUTO MODE (scrape-modal panel + localStorage) ───────
const autoModePrefsKey = 'manga-auto-mode-v1';
function getAutoModePrefs() {
  const v = (id, def) => {
    const el = document.getElementById(id);
    return el ? !!el.checked : !!def;
  };
  return {
    enabled: v('automode-master', true),
    translate: v('automode-translate', true),
    smartfont: v('automode-smartfont', true),
    stroke: v('automode-stroke', true),
    export: v('automode-export', true),
  };
}
function loadAutoModePrefs() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(autoModePrefsKey) || 'null'); } catch (e) { saved = null; }
  if (!saved) return getAutoModePrefs();
  const set = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.checked = !!val; };
  set('automode-master', saved.enabled);
  set('automode-translate', saved.translate);
  set('automode-smartfont', saved.smartfont);
  set('automode-stroke', saved.stroke);
  set('automode-export', saved.export);
  applyAutoMasterState();
  return getAutoModePrefs();
}
function applyAutoMasterState() {
  const on = document.getElementById('automode-master')?.checked !== false;
  for (const id of ['automode-translate', 'automode-smartfont', 'automode-stroke', 'automode-export']) {
    const el = document.getElementById(id);
    if (el) el.disabled = !on;
  }
}
function saveAutoModePrefs() {
  try { localStorage.setItem(autoModePrefsKey, JSON.stringify(getAutoModePrefs())); } catch (e) {}
  applyAutoMasterState();
}
function isAutoModeEnabled() {
  try {
    const saved = JSON.parse(localStorage.getItem(autoModePrefsKey) || 'null');
    if (saved && saved.enabled === false) return false;
  } catch (e) {}
  const el = document.getElementById('automode-master');
  return el ? !!el.checked : true;
}

async function translateChapterViaProvider(slug, chapter) {
  const pid = document.getElementById('scrape-provider')?.value || selectedProvider;
  const model = document.getElementById('scrape-provider-model')?.value || selectedProviderModel;
  if (!pid || !model) {
    toast('اختر مزود الترجمة والنموذج أولاً (زر API)', 'warning');
    return null;
  }
  const tres = await fetch(
    `/api/chapter/${slug}/${chapter}/translate-with-provider?provider_id=${encodeURIComponent(pid)}&model=${encodeURIComponent(model)}&clear=true`,
    { method: 'POST' }
  );
  return await tres.json();
}

async function startBatchScrape() {
  const batchUrls = (document.getElementById('scrape-urls-batch')?.value || '')
    .split('\n').map(s => s.trim()).filter(Boolean);
  const singleUrl = (document.getElementById('scrape-url')?.value || '').trim();
  const startUrlField = (document.getElementById('scrape-start-url')?.value || '').trim();
  // start_url mode when no explicit urls list: use start-url field, fallback to single url input
  const start_url = startUrlField || (batchUrls.length === 0 ? singleUrl : '') || null;
  const urls = batchUrls.length > 0 ? batchUrls : [];
  if (urls.length === 0 && !start_url) { toast('أدخل رابطاً واحداً على الأقل', 'warning'); return; }
  const countRaw = parseInt(document.getElementById('scrape-count')?.value || '1', 10) || 1;
  const headless = document.getElementById('scrape-headless')?.checked !== false;
  const browser = document.getElementById('scrape-browser')?.value || 'brave';
  const autoPrefs = getAutoModePrefs();
  try { saveAutoModePrefs(); } catch (e) {}
  let auto_translate = null;
  if (autoPrefs.enabled && autoPrefs.translate) {
    const pid = document.getElementById('scrape-provider')?.value || selectedProvider;
    const model = document.getElementById('scrape-provider-model')?.value || selectedProviderModel;
    const fallback = getScrapeAutoTranslate();
    const usePid = pid || fallback?.provider_id;
    const useModel = model || fallback?.model;
    if (usePid && useModel) auto_translate = { provider_id: usePid, model: useModel, smart_font: !!autoPrefs.smartfont, unify_stroke: !!autoPrefs.stroke };
  }
  let auto_export = null;
  if (autoPrefs.enabled && autoPrefs.export) {
    const q = parseInt(document.getElementById('export-quality')?.value || '90', 10) || 90;
    const mh = parseInt(document.getElementById('export-max-height')?.value || '9000', 10) || 9000;
    auto_export = { format: 'webp', quality: q, merge: true, max_height: mh };
  }
  const status = document.getElementById('batch-status');
  status.classList.remove('hidden');
  status.textContent = urls.length > 0
    ? `بدء دفعة من ${urls.length} رابط...\n`
    : `بدء دفعة من start_url (count=${countRaw})...\n`;
  try {
    const payload = {
      urls, headless, browser,
      start_url,
      auto_translate,
      auto_export,
      delay_sec: 3,
    };
    // count يُرسل مع start_url فقط (إن كانت urls فارغة)
    if (urls.length === 0 && start_url) payload.count = countRaw;
    const res = await fetch('/api/batch-scrape', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    const batchId = data.batch_id || data.id || data.task_id;
    if (!batchId) {
      status.textContent += '\n✗ تعذر بدء الدفعة: ' + JSON.stringify(data);
      toast('تعذر بدء الدفعة', 'error');
      return;
    }
    toast('بدأت الدفعة — جاري تتبع التقدم', 'info');
    await pollBatchStatus(batchId);
  } catch (e) {
    status.textContent += `\n✗ خطأ: ${e.message}`;
    toast('خطأ في الدفعة', 'error');
  }
}

function batchJobId(j, idx) {
  return j.job_id ?? j.id ?? j.task_id ?? String(idx);
}
async function cancelBatchJob(jobId, batchId) {
  try {
    const res = await fetch(`/api/queue/cancel/${encodeURIComponent(jobId)}`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.text()).slice(0, 200) || 'فشل الإلغاء');
    toast(`تم إلغاء المهمة ${jobId}`, 'success');
  } catch (e) {
    toast('خطأ في الإلغاء: ' + e.message, 'error');
  }
}
async function retryBatchJob(jobId, batchId) {
  try {
    const res = await fetch(`/api/batch/${encodeURIComponent(batchId)}/retry-failed`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.text()).slice(0, 200) || 'فشل إعادة المحاولة');
    toast(`أُعيدت المهمة ${jobId} إلى الطابور`, 'success');
  } catch (e) {
    toast('خطأ في إعادة المحاولة: ' + e.message, 'error');
  }
}
async function refreshQueueStatus(batchId) {
  const status = document.getElementById('batch-status');
  try {
    const res = await fetch('/api/queue');
    const q = await res.json();
    const jobs = q.jobs || q.results || q.queue || [];
    toast(`الطابور: ${jobs.length} مهمة`, 'info');
    if (status && jobs.length && batchId) {
      // Re-render queue snapshot below current batch view
      const div = document.createElement('div');
      div.style.marginTop = '8px';
      div.textContent = `— لقطة الطابور (${jobs.length}) —\n` + jobs.slice(0, 20).map((j, i) =>
        `${batchJobId(j, i)}: ${j.status || q.status || '?'}`).join('\n');
      status.appendChild(div);
    }
  } catch (e) {
    toast('خطأ في جلب الطابور: ' + e.message, 'error');
  }
}
function escHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function escJs(s) {
  return String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}
async function pollBatchStatus(batchId) {
  const status = document.getElementById('batch-status');
  const TERMINAL = new Set(['review', 'exported', 'failed', 'cancelled']);
  const SUCCESS = new Set(['review', 'exported']);
  const deadline = Date.now() + 30 * 60 * 1000; // 30 دقيقة حد أقصى
  while (true) {
    if (Date.now() > deadline) {
      status.innerHTML += '\n⏱ انتهت مهلة التتبع (30 دقيقة) — توقف الاستطلاع. حدّث يدوياً.';
      toast('انتهت مهلة تتبع الدفعة (30 دقيقة)', 'warning');
      break;
    }
    await new Promise(r => setTimeout(r, 2000));
    let info;
    try {
      const res = await fetch(`/api/batch/${batchId}`);
      info = await res.json();
    } catch (e) { continue; }
    const jobs = info.jobs || info.results || [];
    const total = info.total ?? jobs.length ?? '?';
    const completed = jobs.filter(j => SUCCESS.has(j.status)).length;
    const finished = info.finished ?? completed;
    const failed = jobs.filter(j => j.status === 'failed' || j.status === 'cancelled').length;
    const done = (jobs.length > 0 && jobs.every(j => TERMINAL.has(j.status || '')))
      || info.done === true
      || (info.finished != null && info.total != null && info.finished === info.total);
    const progress = info.progress != null ? ` (${info.progress}%)` : '';
    const header = `الدفعة ${escHtml(batchId)}\nالحالة: ${escHtml(info.status || '')}${progress}\n${finished}/${total} مكتمل (ناجح: ${completed}، فاشل/ملغي: ${failed})\n${escHtml(info.log || info.message || '')}`;
    // Render jobs with per-job cancel/retry buttons + queue refresh
    const rows = jobs.map((j, idx) => {
      const jid = batchJobId(j, idx);
      const st = j.status || '؟';
      const label = `${escHtml(jid)}: ${escHtml(st)}${j.slug ? ` — ${escHtml(j.slug)}/${escHtml(j.chapter ?? '')}` : ''}${j.error ? ` — ${escHtml(j.error)}` : ''}`;
      const isTerminal = TERMINAL.has(st);
      const isFailed = st === 'failed' || st === 'cancelled';
      const cancelBtn = !isTerminal
        ? ` <button class="action-btn" style="padding:2px 8px;font-size:11px;" onclick="cancelBatchJob('${escJs(jid)}','${escJs(batchId)}')">إلغاء</button>` : '';
      const retryBtn = isFailed
        ? ` <button class="action-btn" style="padding:2px 8px;font-size:11px;" onclick="retryBatchJob('${escJs(jid)}','${escJs(batchId)}')">إعادة</button>` : '';
      return `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:2px 0;border-bottom:1px solid var(--border);"><span>${label}</span><span style="white-space:nowrap;">${cancelBtn}${retryBtn}</span></div>`;
    }).join('');
    status.innerHTML = `<div style="white-space:pre-wrap;">${escHtml(header)}</div>`
      + (rows ? `<div style="margin-top:8px;max-height:120px;overflow-y:auto;">${rows}</div>` : '')
      + `<div style="margin-top:8px;display:flex;gap:8px;">`
      + `<button class="action-btn" style="padding:2px 8px;font-size:11px;" onclick="refreshQueueStatus('${escJs(batchId)}')">تحديث الطابور (GET /api/queue)</button>`
      + `</div>`;
    if (done) {
      const okJobs = jobs.filter(j => SUCCESS.has(j.status));
      status.innerHTML += `<div style="margin-top:8px;white-space:pre-wrap;">✓ انتهت الدفعة: ناجح ${okJobs.length}/${jobs.length || total} — فاشل/ملغي ${failed}</div>`;
      toast(`انتهت الدفعة: ناجح ${okJobs.length} / فاشل ${failed}`, okJobs.length ? 'success' : 'error');
      await loadChapterList();
      const first = okJobs[0] || (jobs.find(j => j.slug && j.chapter)) || (info.results || [])[0] || info.first || null;
      if (first && first.slug && first.chapter) await loadChapter(first.slug, first.chapter);
      break;
    }
  }
}
async function startScrape() {
  const url = document.getElementById('scrape-url').value.trim();
  if (!url) { toast('أدخل رابط الفصل', 'warning'); return; }

  const status = document.getElementById('scrape-status');
  status.classList.remove('hidden');
  status.textContent = 'بدء التحميل...\n';

  const translate = document.getElementById('scrape-translate').checked;
  const headless = document.getElementById('scrape-headless').checked;
  const browser = document.getElementById('scrape-browser').value;

  try {
    const res = await fetch(`/api/scrape?url=${encodeURIComponent(url)}&headless=${headless}&browser=${browser}`, { method: 'POST' });
    const data = await res.json();

    if (data.status !== 'started') {
      status.textContent += `\n✗ خطأ: تعذر بدء التحميل`;
      toast('تعذر بدء التحميل', 'error');
      return;
    }

    const taskId = data.task_id;
    toast(`بدأ التحميل في الخلفية (${browser})`, 'info');

    // Poll for status
    let lastLogSize = 0;
    while (true) {
      await new Promise(r => setTimeout(r, 1500));
      let task;
      try {
        const tres = await fetch(`/api/scrape/task/${taskId}`);
        task = await tres.json();
      } catch (e) {
        continue;
      }
      if (task.log && task.log.length > lastLogSize) {
        status.textContent = task.log;
        lastLogSize = task.log.length;
      }
      if (task.done) {
        if (task.status === 'ok') {
          status.textContent += '\n✓ تم التحميل بنجاح!';
          toast('تم تحميل الفصل بنجاح', 'success');

          if (translate && task.slug && task.chapter) {
            status.textContent += '\nجاري الترجمة عبر API...';
            const tdata = await translateChapterViaProvider(task.slug, task.chapter);
            if (tdata && tdata.status === 'ok') {
              status.textContent += `\n✓ تمت ترجمة ${tdata.translated} نص`;
              toast('تمت الترجمة', 'success');
            } else if (tdata) {
              status.textContent += `\n✗ فشلت الترجمة: ${tdata.message || tdata.detail || ''}`;
              toast('فشلت الترجمة عبر API', 'error');
            }
          }

          await loadChapterList();
          await loadChapter(task.slug, task.chapter);
          setTimeout(() => hideScrapeModal(), 4000);
        } else {
          status.textContent += `\n✗ خطأ: ${task.error || 'فشل'}`;
          toast('فشل التحميل', 'error');
        }
        break;
      }
    }
  } catch (e) {
    status.textContent += `\n✗ خطأ: ${e.message}`;
    toast('خطأ في الاتصال', 'error');
  }
}

// ─── WELCOME STATE ──────────────────────────────────────
function showWelcome() {
  // Show a welcome message when no chapter is loaded
  const container = document.getElementById('canvas-container');
  if (!container.querySelector('.welcome-overlay')) {
    const welcome = document.createElement('div');
    welcome.className = 'welcome-overlay';
    welcome.innerHTML = `
      <div style="text-align:center; padding:60px 20px;">
        <h2 style="font-size:22px; font-weight:600; margin-bottom:8px;">Manga AI Editor</h2>
        <p style="color:var(--text-dim); font-size:14px; margin-bottom:24px;">
          حمل فصلاً لبدء التحرير
        </p>
        <button class="menu-btn" onclick="showChapterSelector()" style="font-size:16px; padding:12px 28px; background:var(--accent); border-radius:8px;">
          فتح فصل موجود
        </button>
        <span style="margin:0 8px; color:var(--text-dim);">أو</span>
        <button class="menu-btn" onclick="showScrapeModal()" style="font-size:16px; padding:12px 28px; border:2px solid var(--accent); border-radius:8px;">
          تحميل فصل جديد
        </button>
      </div>
    `;
    container.appendChild(welcome);
  }
}

// ─── SHUTDOWN ───────────────────────────────────────────
// (Local model management removed — translation is provider-only via API)

// ─── SHUTDOWN ───────────────────────────────────────────
// (Local model management removed — translation is provider-only via API)
async function shutdownServer() {
  if (!confirm('هل تريد إيقاف تشغيل التطبيق؟')) return;
  try {
    await fetch('/api/shutdown', { method: 'POST' });
    toast('جاري إيقاف التطبيق...', 'info');
    setTimeout(() => {
      document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;color:var(--text);background:var(--bg)"><p>تم إيقاف التطبيق. يمكنك إغلاق النافذة.</p></div>';
    }, 1000);
  } catch (e) {
    toast('فشل إيقاف التطبيق', 'error');
  }
}

// ─── AUTO TRANSLATE (provider-only, direct) ───────────────
function countUntranslated() {
  if (!chapterData) return 0;
  let n = 0;
  for (const p of (chapterData.pages || []))
    for (const t of (p.texts || []))
      if (!((t.arabic_text || '').trim())) n++;
  return n;
}

async function translateMissingOnce() {
  const res = await fetch(
    `/api/chapter/${currentSlug}/${currentChapter}/translate-with-provider?provider_id=${encodeURIComponent(selectedProvider)}&model=${encodeURIComponent(selectedProviderModel)}&clear=false`,
    { method: 'POST' }
  );
  const data = await res.json();
  if (data.status === 'ok') {
    const res2 = await fetch(`/api/chapter/${currentSlug}/${currentChapter}`);
    chapterData = await res2.json();
    if (viewMode === 'single') await renderSinglePage(currentPage);
    else await renderWebtoon();
    autoFittedKey = '';
    autoFitOnceForChapter();
  }
  return data;
}

async function refetchTexts() {
  // Slow-network saver: re-fetch overlay texts+geometry only.
  // Images come from local cache; translations/styles are restored by id.
  if (!chapterData) { toast('افتح فصلاً أولاً', 'warning'); return; }
  if (!confirm('إعادة جلب النصوص من الموقع؟ (الترجمة الحالية محفوظة، الصور لن تُحمّل مجدداً)')) return;
  toast('جاري جلب النصوص... (قد يأخذ دقيقة)', 'info', 90000);
  try {
    const res = await fetch(
      `/api/chapter/${currentSlug}/${currentChapter}/refetch-texts`,
      { method: 'POST' }
    );
    const data = await res.json();
    if (data.status === 'ok') {
      toast(`تم: ${data.found} نصاً، استُعيدت ترجمة ${data.restored}`, 'success');
      const res2 = await fetch(`/api/chapter/${currentSlug}/${currentChapter}`);
      chapterData = await res2.json();
      autoFittedKey = '';
      if (viewMode === 'single') await renderSinglePage(currentPage);
      else await renderWebtoon();
      autoFitOnceForChapter();
    } else {
      toast('فشل الجلب: ' + (data.detail || JSON.stringify(data)), 'error');
    }
  } catch (e) {
    toast('خطأ في الجلب (نت بطيء؟ أعد المحاولة): ' + e.message, 'error');
  }
}

async function retryTranslate() {  if (!chapterData) { toast('افتح فصلاً أولاً', 'warning'); return; }
  if (!selectedProvider || !selectedProviderModel) {
    toast('اختر مزود الترجمة والنموذج من زر API أولاً', 'warning');
    showProviderModal();
    return;
  }
  let round = 1;
  for (;;) {
    const remaining = countUntranslated();
    if (remaining === 0) { toast('لا توجد نصوص ناقصة — كل شيء مترجم', 'success'); return; }
    toast(`محاولة ${round}: ترجمة ${remaining} نصاً ناقصاً...`, 'info', 60000);
    try {
      const data = await translateMissingOnce();
      if (data.status === 'ok') {
        const left = countUntranslated();
        toast(`المحاولة ${round}: تُرجم ${data.translated} — بقي ${left}`, left ? 'warning' : 'success');
        if (left === 0) return;
        round++;
        if (!confirm(`بقي ${left} نصاً بدون ترجمة — إعادة المحاولة؟`)) return;
      } else {
        toast('فشلت المحاولة: ' + (data.message || data.detail || JSON.stringify(data)), 'error');
        if (!confirm('فشلت الترجمة — المحاولة مرة أخرى؟')) return;
        round++;
      }
    } catch (e) {
      toast('خطأ في الترجمة: ' + e.message, 'error');
      if (!confirm('حدث خطأ — المحاولة مرة أخرى؟')) return;
      round++;
    }
  }
}

async function autoTranslate() {
  if (!chapterData) { toast('افتح فصلاً أولاً', 'warning'); return; }
  if (!selectedProvider || !selectedProviderModel) {
    toast('اختر مزود الترجمة والنموذج من زر API أولاً', 'warning');
    showProviderModal();
    return;
  }
  const missing = countUntranslated();
  if (missing === 0) {
    if (!confirm('كل النصوص مترجمة — إعادة ترجمة الكل من جديد؟ (سيمسح الحالية)')) return;
    toast(`جاري الترجمة عبر ${selectedProvider}...`, 'info', 60000);
    try {
      const res = await fetch(
        `/api/chapter/${currentSlug}/${currentChapter}/translate-with-provider?provider_id=${encodeURIComponent(selectedProvider)}&model=${encodeURIComponent(selectedProviderModel)}&clear=true`,
        { method: 'POST' }
      );
      const data = await res.json();
      if (data.status === 'ok') {
        toast(`تمت ترجمة ${data.translated} نص عبر ${data.provider || selectedProvider}`, 'success');
        const res2 = await fetch(`/api/chapter/${currentSlug}/${currentChapter}`);
        chapterData = await res2.json();
        if (viewMode === 'single') await renderSinglePage(currentPage);
        else await renderWebtoon();
        autoFittedKey = '';
        autoFitOnceForChapter();
      } else {
        toast('فشلت الترجمة: ' + (data.message || data.detail || JSON.stringify(data)), 'error');
      }
    } catch (e) {
      toast('خطأ في الترجمة: ' + e.message, 'error');
    }
    return;
  }
  if (!confirm(`ترجمة ${missing} نصاً ناقصاً عبر ${selectedProvider} / ${selectedProviderModel}؟`)) return;
  await retryTranslate();
}

// ─── EXPORT MODAL ────────────────────────────────────────
function collectExportSettings() {
  return {
    format: document.getElementById('export-format')?.value || 'webp',
    quality: parseInt(document.getElementById('export-quality')?.value || '90', 10) || 90,
    font_scale: parseFloat(document.getElementById('export-font-scale')?.value || '1.0') || 1.0,
    line_gap: parseInt(document.getElementById('export-line-gap')?.value || '2', 10) || 0,
    bg_color: document.getElementById('export-bg-color')?.value || '#ffffff',
    force_stroke: !!document.getElementById('export-force-stroke')?.checked,
    export_stroke_w: parseFloat(document.getElementById('export-stroke-w')?.value || '1.5') || 1.5,
    export_stroke_color: document.getElementById('export-stroke-color')?.value || '#ffffff',
    merge: !!document.getElementById('export-merge')?.checked,
    max_height: parseInt(document.getElementById('export-max-height')?.value || '9000', 10) || 9000,
  };
}
function applyExportSettings(s) {
  if (!s) return;
  const setVal = (id, v) => { const el = document.getElementById(id); if (el && v !== undefined && v !== null) el.value = v; };
  const setChk = (id, v) => { const el = document.getElementById(id); if (el && v !== undefined && v !== null) el.checked = !!v; };
  if (s.format !== undefined) setVal('export-format', s.format);
  if (s.quality !== undefined) setVal('export-quality', s.quality);
  if (s.font_scale !== undefined) setVal('export-font-scale', s.font_scale);
  if (s.line_gap !== undefined) setVal('export-line-gap', s.line_gap);
  if (s.bg_color !== undefined) { setVal('export-bg-color', s.bg_color); setVal('export-bg-hex', s.bg_color); }
  if (s.force_stroke !== undefined) setChk('export-force-stroke', s.force_stroke);
  if (s.export_stroke_w !== undefined) setVal('export-stroke-w', s.export_stroke_w);
  if (s.export_stroke_color !== undefined) { setVal('export-stroke-color', s.export_stroke_color); setVal('export-stroke-hex', s.export_stroke_color); }
  if (s.merge !== undefined) setChk('export-merge', s.merge);
  if (s.max_height !== undefined) setVal('export-max-height', s.max_height);
  const qv = document.getElementById('export-quality-val');
  if (qv) qv.textContent = document.getElementById('export-quality').value;
  const fsv = document.getElementById('export-font-scale-val');
  if (fsv) fsv.textContent = parseFloat(document.getElementById('export-font-scale').value || '1').toFixed(2);
  const lgv = document.getElementById('export-line-gap-val');
  if (lgv) lgv.textContent = document.getElementById('export-line-gap').value;
  const swv = document.getElementById('export-stroke-w-val');
  if (swv) swv.textContent = document.getElementById('export-stroke-w').value;
  const mhv = document.getElementById('export-max-height-val');
  if (mhv) mhv.textContent = document.getElementById('export-max-height').value;
}
async function loadExportSettings() {
  try {
    const res = await fetch('/api/settings/export');
    if (!res.ok) return null;
    const data = await res.json();
    const s = data.settings || data.data || data;
    applyExportSettings(s);
    return s;
  } catch (e) { return null; }
}
async function saveExportSettings() {
  try {
    await fetch('/api/settings/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectExportSettings()),
    });
  } catch (e) {}
}
function showExportModal() {
  if (!chapterData) { toast('افتح فصلاً أولاً', 'warning'); return; }
  document.getElementById('export-modal').classList.remove('hidden');
  document.getElementById('export-range-to').max = totalPages;
  document.getElementById('export-range-to').value = totalPages;
  document.getElementById('export-progress').classList.add('hidden');
  // Estimate total height if merging
  if (chapterData.pages && chapterData.pages.length > 0) {
    const avgH = chapterData.pages.reduce((s, p) => s + (p.height || 1500), 0) / chapterData.pages.length;
    const totalEst = Math.round(avgH * chapterData.pages.length);
    document.getElementById('export-max-height').max = Math.max(totalEst, 9000);
  }
  loadExportSettings().catch(() => {});
}
function hideExportModal() {
  document.getElementById('export-modal').classList.add('hidden');
}
function toggleExportRange() {
  const val = document.querySelector('input[name="export-range"]:checked')?.value;
  document.getElementById('export-range-inputs').classList.toggle('hidden', val !== 'range');
}

async function startExport() {
  const saved = await saveAll();
  if (!saved) return;

  const fmt = document.getElementById('export-format').value;
  const quality = parseInt(document.getElementById('export-quality').value);
  const merge = document.getElementById('export-merge').checked;
  const maxHeight = parseInt(document.getElementById('export-max-height').value);
  const rangeVal = document.querySelector('input[name="export-range"]:checked')?.value || 'all';

  const progress = document.getElementById('export-progress');
  progress.classList.remove('hidden');
  progress.textContent = 'جاري التصدير...';

  let pages = [];
  if (rangeVal === 'current') pages = [currentPage];
  else if (rangeVal === 'range') {
    const from = parseInt(document.getElementById('export-range-from').value);
    const to = parseInt(document.getElementById('export-range-to').value);
    for (let i = from; i <= to; i++) pages.push(i);
  }

  try {
    const params = new URLSearchParams();
    params.set('format', fmt);
    params.set('quality', quality);
    params.set('merge', merge);
    params.set('max_height', maxHeight);
    params.set('font_scale', document.getElementById('export-font-scale').value);
    params.set('line_gap', document.getElementById('export-line-gap').value);
    params.set('bg_color', document.getElementById('export-bg-color').value);
    params.set('force_stroke', document.getElementById('export-force-stroke').checked);
    params.set('export_stroke_w', document.getElementById('export-stroke-w').value);
    params.set('export_stroke_color', document.getElementById('export-stroke-color').value);
    if (pages.length > 0) params.set('pages', pages.join(','));

    const res = await fetch(
      `/api/chapter/${currentSlug}/${currentChapter}/export?${params.toString()}`,
      { method: 'POST' }
    );
    const data = await res.json();
    if (data.status === 'ok') {
      progress.textContent = `✓ تم تصدير ${data.files?.length || 0} ملف`;
      try { await saveExportSettings(); } catch (e) {}

      if (data.files?.length > 0) {
        if (data.files.length === 1) {
          const url = `/api/chapter/${currentSlug}/${currentChapter}/exported/${encodeURIComponent(data.files[0])}`;
          window.open(url, '_blank');
        } else {
          // Download ZIP for multiple files
          const zipParams = new URLSearchParams();
          zipParams.set('format', fmt);
          zipParams.set('quality', quality);
          zipParams.set('merge', merge);
          zipParams.set('max_height', maxHeight);
          const zipRes = await fetch(
            `/api/chapter/${currentSlug}/${currentChapter}/download-zip?${zipParams.toString()}`,
            { method: 'GET' }
          );
          if (zipRes.ok) {
            const blob = await zipRes.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${currentSlug}_chapter_${currentChapter}.zip`;
            a.click();
            URL.revokeObjectURL(url);
            progress.textContent += ' ✓ تم تحميل ZIP';
          }
        }
      }
    } else {
      progress.textContent = `✗ خطأ: ${JSON.stringify(data)}`;
    }
  } catch (e) {
    progress.textContent = `✗ ${e.message}`;
  }

  setTimeout(() => {
    progress.classList.add('hidden');
    hideExportModal();
  }, 5000);
}

// ─── HEX INPUT HANDLERS ──────────────────────────────────
function onColorHexInput() {
  const hex = document.getElementById('prop-color-hex').value.trim();
  if (/^#[0-9a-f]{6}$/i.test(hex)) {
    document.getElementById('prop-color').value = hex;
    onPropChange();
  }
}
function onStrokeHexInput() {
  const hex = document.getElementById('prop-stroke-hex').value.trim();
  if (/^#[0-9a-f]{6}$/i.test(hex)) {
    document.getElementById('prop-stroke-color').value = hex;
    onPropChange();
  }
}
document.getElementById('prop-color-hex').addEventListener('change', onColorHexInput);
document.getElementById('prop-stroke-hex').addEventListener('change', onStrokeHexInput);

// ─── PROVIDER MANAGEMENT ──────────────────────────────
let selectedProvider = null;
let selectedProviderModel = null;

function showProviderModal() {
  document.getElementById('provider-modal').classList.remove('hidden');
  loadProvidersList();
}
function hideProviderModal() {
  document.getElementById('provider-modal').classList.add('hidden');
}

async function loadProvidersList() {
  try {
    const res = await fetch('/api/providers');
    const providers = await res.json();
    const container = document.getElementById('providers-list');
    container.innerHTML = '';

    // Restore saved provider selection
    try {
      const saved = localStorage.getItem('selected_provider');
      if (saved) {
        const [pid, pmodel] = saved.split('||');
        selectedProvider = pid;
        selectedProviderModel = pmodel;
      }
    } catch(e) {}

    // Show current mode indicator (provider-only, direct)
    const modeIndicator = document.createElement('div');
    modeIndicator.style.cssText = 'padding:8px 12px; margin-bottom:12px; background:var(--bg-input); border-radius:var(--radius); font-size:12px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;';
    if (selectedProvider) {
      modeIndicator.innerHTML = `
        <span>🧠 وضع الترجمة: <strong style="color:var(--accent)">${selectedProvider} / ${selectedProviderModel || '—'}</strong> (عبر الإنترنت — مباشر)</span>
        <button class="action-btn" style="font-size:11px; padding:2px 8px;" onclick="clearProviderSelection()">إلغاء الاختيار</button>
      `;
    } else {
      modeIndicator.innerHTML = `
        <span>🧠 وضع الترجمة: <strong style="color:var(--warning)">اختر مزود API للترجمة المباشرة</strong></span>
      `;
    }
    container.appendChild(modeIndicator);

    for (const [pid, p] of Object.entries(providers)) {
      if (pid === 'custom') continue; // handled separately

      const isActive = selectedProvider === pid;
      const hasKey = p.has_key;
      const div = document.createElement('div');
      div.className = 'model-item';
      div.style.marginBottom = '8px';

      let modelsHtml = '';
      if (hasKey || p.enabled) {
        const modelList = p.models || [];
        modelsHtml = `
          <div style="margin-top:6px; display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
            <select id="provider-model-${pid}" style="flex:3; min-width:150px; height:30px; padding:0 6px; background:var(--bg-input); border:1px solid var(--border); color:var(--text); border-radius:var(--radius); font-size:12px;" onchange="onProviderModelChange('${pid}')">
              ${modelList.length === 0 ? '<option value="">— جلب النماذج —</option>' : ''}
              ${modelList.map(m => `<option value="${m.id}" ${m.id === (p.default_model || selectedProviderModel) ? 'selected' : ''}>${m.name}</option>`).join('')}
            </select>
            <button class="action-btn" style="font-size:11px; padding:2px 8px; height:30px;" onclick="fetchProviderModels('${pid}')">جلب</button>
            <button class="action-btn" style="font-size:11px; padding:2px 8px; height:30px;" onclick="testProviderConnection('${pid}')">اختبار</button>
          </div>
        `;
      }

      const keyDisplay = hasKey ? '••••••••' : '';
      div.innerHTML = `
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          <div style="flex:1; min-width:120px;">
            <div style="font-size:13px; font-weight:600;">${p.name}</div>
            <div style="font-size:11px; color:var(--text-dim);">
              ${hasKey ? '<span style="color:var(--success)">✓ Key محفوظ</span>' : '<span style="color:var(--text-dim)">لا يوجد Key</span>'}
              ${isActive ? '• <strong style="color:var(--accent)">قيد الاستخدام</strong>' : ''}
              ${pid === 'groq' ? '• <span style="color:var(--accent)">مجاني</span>' : ''}
            </div>
          </div>
          <input type="password" id="provider-key-${pid}" placeholder="${hasKey ? 'Key موجود — اكتب جديداً للتحديث' : 'API Key'}" data-haskey="${hasKey}" value="" style="width:220px; height:30px; padding:0 8px; background:var(--bg-input); border:1px solid var(--border); color:var(--text); border-radius:var(--radius); font-size:12px;">
          <button class="model-btn model-btn-use" onclick="saveProviderKey('${pid}')">${hasKey ? 'تحديث' : 'حفظ'}</button>
          <button class="model-btn ${isActive ? 'model-btn-active' : 'model-btn-use'}" onclick="useProvider('${pid}')">
            ${isActive ? '✓ النشط' : 'استخدام'}
          </button>
        </div>
        ${modelsHtml}
      `;
      container.appendChild(div);
    }

    // Active provider label
    const label = document.getElementById('active-provider-label');
    if (selectedProvider) {
      label.textContent = `النشط: ${selectedProvider} / ${selectedProviderModel || '—'}`;
    } else {
      label.textContent = 'غير محدد — اختر مزود API';
    }
  } catch(e) {
    console.error('Provider load error:', e);
    const container = document.getElementById('providers-list');
    if (container && !container.innerHTML.trim()) {
      container.innerHTML = `<div style="padding:10px; background:var(--bg-input); border-radius:var(--radius); font-size:12px; color:var(--error, #e74c3c);">تعذر تحميل قائمة المزودين من السيرفر: ${String(e.message || e)}<br>تأكد أن السيرفر يعمل بآخر نسخة ثم حدّث الصفحة (Ctrl+F5).</div>`;
    }
  }
}

function clearProviderSelection() {
  selectedProvider = null;
  selectedProviderModel = null;
  try { localStorage.removeItem('selected_provider'); } catch(e) {}
  toast('تم إلغاء اختيار المزود', 'info');
  loadProvidersList();
}

async function saveProviderKey(pid) {
  const input = document.getElementById(`provider-key-${pid}`);
  const key = input.value.trim();
  // Don't re-save if field shows placeholder dots (user didn't change it)
  if (!key || key === '') {
    const hasKey = document.querySelector(`#provider-key-${pid}`)?.dataset?.haskey === 'true';
    if (hasKey) {
      toast('المفتاح موجود بالفعل', 'info');
      return;
    }
    toast('أدخل API Key', 'warning');
    return;
  }
  try {
    await fetch(`/api/providers/${pid}/save`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ api_key: key, enabled: true }),
    });
    toast(`تم حفظ مفتاح ${pid}`, 'success');
    // Auto-fetch models
    await fetchProviderModels(pid);
    loadProvidersList();
  } catch(e) {
    toast('خطأ في الحفظ', 'error');
  }
}

async function testProviderConnection(pid) {
  const status = document.getElementById('provider-status');
  status.classList.remove('hidden');
  status.textContent = 'جاري اختبار الاتصال...';
  try {
    const res = await fetch(`/api/providers/${pid}/test`, { method: 'POST' });
    const data = await res.json();
    status.textContent = data.status === 'ok' ? '✓ اتصال ناجح' : `✗ ${data.message}`;
    status.style.color = data.status === 'ok' ? 'var(--success)' : 'var(--danger)';
  } catch(e) {
    status.textContent = '✗ فشل الاتصال';
  }
  setTimeout(() => status.classList.add('hidden'), 3000);
}

async function fetchProviderModels(pid) {
  const status = document.getElementById('provider-status');
  status.classList.remove('hidden');
  status.textContent = 'جاري جلب النماذج...';
  try {
    const res = await fetch(`/api/providers/${pid}/fetch-models`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok' && data.models) {
      // Save to server
      await fetch(`/api/providers/${pid}/save`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ models: data.models }),
      });
      status.textContent = `✓ تم جلب ${data.models.length} نموذج`;
      loadProvidersList();
    } else {
      status.textContent = `✗ ${data.message || 'فشل الجلب'}`;
    }
  } catch(e) {
    status.textContent = '✗ خطأ في الاتصال';
  }
  setTimeout(() => status.classList.add('hidden'), 4000);
}

function onProviderModelChange(pid) {
  const sel = document.getElementById(`provider-model-${pid}`);
  if (sel && selectedProvider === pid) {
    selectedProviderModel = sel.value;
    try { localStorage.setItem('selected_provider', `${pid}||${selectedProviderModel}`); } catch(e) {}
  }
}

function useProvider(pid) {
  const sel = document.getElementById(`provider-model-${pid}`);
  selectedProvider = pid;
  selectedProviderModel = sel ? sel.value : '';
  try { localStorage.setItem('selected_provider', `${pid}||${selectedProviderModel}`); } catch(e) {}
  toast(`تم اختيار: ${pid} / ${selectedProviderModel}`, 'success');
  loadProvidersList();
}

async function addCustomProvider() {
  const baseUrl = document.getElementById('custom-base-url').value.trim();
  const apiKey = document.getElementById('custom-api-key').value.trim();
  if (!baseUrl || !apiKey) { toast('أدخل الرابط والمفتاح', 'warning'); return; }
  try {
    await fetch('/api/providers/custom/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, enabled: true }),
    });
    toast('تمت إضافة الـ API المخصص', 'success');
    await fetchProviderModels('custom');
    document.getElementById('custom-base-url').value = '';
    document.getElementById('custom-api-key').value = '';
    loadProvidersList();
  } catch(e) {
    toast('خطأ في الإضافة', 'error');
  }
}

// ─── TEXT EXCHANGE (export / import) ────────────────────
async function exportChapterText() {
  if (!chapterData) { toast('افتح فصلاً أولاً', 'warning'); return; }
  try {
    const res = await fetch(`/api/chapter/${currentSlug}/${currentChapter}/text-export`);
    let text = '';
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      const data = await res.json();
      text = data.text || data.content || JSON.stringify(data, null, 2);
    } else {
      text = await res.text();
    }
    if (!res.ok) throw new Error(text.slice(0, 200) || 'فشل التصدير');
    lastExportedText = text;
    lastExportedFilename = `${currentSlug}_chapter_${currentChapter}.txt`;
    document.getElementById('text-export-content').value = text;
    document.getElementById('text-export-modal').classList.remove('hidden');
    toast('تم جلب النص — انسخ أو حمّل الملف', 'success');
  } catch (e) {
    toast('خطأ في تصدير النص: ' + e.message, 'error');
  }
}
function hideTextExportModal() {
  document.getElementById('text-export-modal').classList.add('hidden');
}
function copyExportedText() {
  const ta = document.getElementById('text-export-content');
  ta.select();
  try { document.execCommand('copy'); } catch (e) {}
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(ta.value).catch(() => {});
  }
  toast('تم النسخ', 'success');
}
function downloadExportedText() {
  const blob = new Blob([lastExportedText || document.getElementById('text-export-content').value || ''], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = lastExportedFilename;
  a.click();
  URL.revokeObjectURL(url);
}
function showTextImportModal() {
  if (!chapterData) { toast('افتح فصلاً أولاً', 'warning'); return; }
  document.getElementById('text-import-content').value = '';
  document.getElementById('text-import-report').classList.add('hidden');
  document.getElementById('text-import-modal').classList.remove('hidden');
}
function hideTextImportModal() {
  document.getElementById('text-import-modal').classList.add('hidden');
}
async function submitTextImport() {
  if (!chapterData) return;
  if (guardViewOnly()) return;
  const text = document.getElementById('text-import-content').value;
  if (!text.trim()) { toast('الصق النص أولاً', 'warning'); return; }
  const overwrite = document.getElementById('text-import-overwrite')?.checked || false;
  const report = document.getElementById('text-import-report');
  report.classList.remove('hidden');
  report.textContent = 'جاري الاستيراد...';
  try {
    const res = await fetch(`/api/chapter/${currentSlug}/${currentChapter}/text-import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: text, overwrite }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.message || 'فشل الاستيراد');
    const imported = data.imported ?? data.updated ?? 0;
    const skipped = data.skipped_empty ?? data.skipped ?? 0;
    const unknown = data.unknown_id ?? data.not_found ?? data.unknown ?? 0;
    report.textContent = `تقرير الاستيراد:\nمستورد: ${imported}\nمتخطى: ${skipped}\nغير معروف: ${unknown}`;
    toast(`تم الاستيراد: ${imported} / متخطى ${skipped} / غير معروف ${unknown}`, 'success');
    const res2 = await fetch(`/api/chapter/${currentSlug}/${currentChapter}`);
    chapterData = await res2.json();
    if (viewMode === 'single') await renderSinglePage(currentPage);
    else await renderWebtoon();
  } catch (e) {
    report.textContent = '✗ ' + e.message;
    toast('خطأ في الاستيراد: ' + e.message, 'error');
  }
}

// ─── INITIALIZE ─────────────────────────────────────────
loadFonts();
showWelcome();
// Restore selected provider (direct API translation only)
try {
  const saved = localStorage.getItem('selected_provider');
  if (saved) {
    const [pid, pmodel] = saved.split('||');
    selectedProvider = pid;
    selectedProviderModel = pmodel;
  }
} catch(e) {}
document.body.classList.add('mode-review');

// ─── DATA-LOSS GUARD ──────────────────────────────────────
window.addEventListener('beforeunload', e => {
  if (isDirty) { e.preventDefault(); e.returnValue = ''; }
});

// ─── SETTINGS ────────────────────────────────────────────
async function showSettingsModal() {
  document.getElementById('settings-modal').classList.remove('hidden');
  document.getElementById('settings-status').textContent = '';
  document.getElementById('settings-export-status').textContent = '';
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    document.getElementById('settings-output-dir').value = data.output_dir || '';
  } catch (e) {
    document.getElementById('settings-status').textContent = 'تعذر قراءة الإعدادات';
  }
  try {
    const res = await fetch('/api/settings/export');
    const data = await res.json();
    document.getElementById('settings-export-dir').value = data.export_dir || '';
  } catch (e) {}
}
function hideSettingsModal() {
  document.getElementById('settings-modal').classList.add('hidden');
}
async function saveSettings() {
  const dir = document.getElementById('settings-output-dir').value.trim();
  const st = document.getElementById('settings-status');
  if (!dir) { st.textContent = 'أدخل مسار المجلد'; return; }
  try {
    const res = await fetch('/api/settings/output-dir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ output_dir: dir }),
    });
    const data = await res.json();
    if (data.status === 'ok') {
      st.textContent = 'تم الحفظ: ' + data.output_dir;
      toast('تم تغيير مجلد المانهوا', 'success');
      await loadChapterList();
    } else {
      st.textContent = 'خطأ: ' + (data.detail || JSON.stringify(data));
    }
  } catch (e) {
    st.textContent = 'خطأ: ' + e.message;
  }
}
async function saveExportDir() {
  const dir = document.getElementById('settings-export-dir').value.trim();
  const st = document.getElementById('settings-export-status');
  try {
    const res = await fetch('/api/settings/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ export_dir: dir }),
    });
    const data = await res.json();
    st.textContent = 'مجلد التصدير: ' + (data.export_dir || '(مع الفصول)');
    toast('تم حفظ مجلد التصدير', 'success');
  } catch (e) {
    st.textContent = 'خطأ: ' + e.message;
  }
}
async function clearExportDir() {
  document.getElementById('settings-export-dir').value = '';
  await saveExportDir();
}
