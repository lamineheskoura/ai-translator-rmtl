# عقود الإصلاح الآمن — اقرأ قبل لمس الكود (40 سطراً)

## 1) حقائق الموقع (مقاسة حياً، لا "تصحيح" لها)
- `left` في style = **مركز** الصندوق: `x = left - max-width/2` (يؤكده `translateX(-50%)`).
- `top` مطلق على مستوى المستند ويشمل **هيدر إعلانات H0** + بلا فجوات gap=0.
- المعايرة لكل فصل على حدة: `s` من انحدار Theil-Sen، `H0` من وسيط أصغر البواقي،
  `geo_tops` تشمل الصناديق الفارغة عمداً. لا تُصلّب قيم فصل في فصل آخر.
- الحاوية قد تكون أعرض من الصورة (offset يُقدَّر فقط عند البرهان:
  صناديق أعرض من الصورة، وإلا legacy بدون إزاحة).

## 2) عقد chapter_data.json (لا تُسقط حقولاً)
- النص: `id,page,x,y,x_center,width,height,font_size_px,line_height,
  scale_factor,original_text,arabic_text,style.*` — `x_center` محفوظ عمداً.
- `style.*`: `font,font_size,line_height,color,stroke_color,stroke_width,
  stroke_enabled,align,rotation` — الألوان/الحدود من الموقع كما هي.
- `save_chapter` يعيد بناء الصفحات — أي حقل جديد يُضاف لقائمته صراحة.

## 3) عقد المقاسات (ملاءمة أولاً، ثم +25% إجبارية)
- الموقع/السكرابر يحدد الحجم؛ الملاءمة تصغّر عند الفيض فقط.
- بعد الملاءمة: `AUTO_FIT_BOOST=1.25` في `exporter.py` يضرب الناتج ×1.25
  **بلا إعادة تحقق** (أمر المستخدم الصريح: الزيادة على رقم الملاءمة نفسه).
- `ARABIC_MIN_FONT=14` (مصدّر + محرر): الملاءمة التلقائية لا تنزل تحته
  أبداً للعربي؛ EN لا يُمس (أرضيته = حجم الموقع)؛ التجاوز اليدوي (6+) يبقى.
- النمو ذكي ثنائي البعد: العرض أولاً (متماثل حول المركز، سقف عرض الصفحة
  والجيران الأفقيين)، ثم الطول لأسفل؛ عند انسداد الطول يعوّض العرض
  (العرض بديل الارتفاع). فصل لاحق يضمن فجوة ≥3px (أي ≥1px بعد int).
- الضبط التلقائي عند فتح الفصل في المحرر **لا** يعزز (منع التضاعف
  التراكمي)؛ التعزيز يأتي فقط من زر "ضبط ذكي" (الخادم) وعامل الدفعات.
- قياس المحرر = قياس المصدّر: إزاحة تكيفية (لا 0.96)، طرح الـpadding من
  المقاس (لا عدّ مزدوج)، احتياطي ستروك، line_gap=2، معامل لاتيني 0.8.
- التوسيط في التصدير بموضع الحبر الدقيق: الرسم بإزاحة أصل الـink-bbox
  (مرساة PIL الافتراضية تثبت خط الصاعد لا قمة الحبر — كانت ترفع النص).
- `TEXT_PADDING=6` في المصدّر **و** `padding 6px` في المحرر/القياس — الثلاثة معاً أو لا شيء.
- `rendered_w=800.0` في spider ثابت عمداً (يجبر مسار تقدير PNG).

## 4) عقد المسارات (frontend ↔ backend)
- `cancelBatchJob → POST /api/queue/cancel/{job_id}`
- `retryBatchJob → POST /api/batch/{batchId}/retry-failed`
- `download-zip` يقبل نفس حقول التصدير كاملة.
- أي `fetch('/api/...')` جديد يجب أن يكون له نظير في `editor/server.py`
  (افحص بـ `smoke.bat` قبل الدفع).

## 5) عقد الترجمة
- الحقن حسب `id` فقط (translate/provider/import) — الإحداثيات لا تُمس أبداً.
- النصوص بلا `original_text` لا تُرسل للمزود ولا تُصدَّر نصياً.
- مفاتيح API من `env` أولاً ولا تُحفظ على القرص أبداً.

## 6) قاعدة الملفات المشتركة
- `server.py` / `app.js` / `coordinator.py` تُعدَّل بملكية واحدة في المرة.
- تغيير defaults (دمج/سترُوك) يتم في الطبقات الثلاث معاً أو لا يتم.
- أي إصلاح: `smoke.bat` أخضر + سطر في رسالة الـcommit يذكر العقد.

## 7) Live-verified geometry model (real browser, 2 chapters, Oct 2026)
- left = box CENTER exact to 0.03px, 	op exact - use as-is. Site centers via translateX(-50%).
- H0 = min residual with corroboration (2nd point within 25px), clamped [0,600], else median-of-7 fallback. Verified 275.0/275.0 vs 275.0 live on both chapters.
- width = max-width (canonical block geometry). Live width deviations are prior drag-edits, NOT cloned.
- height = style height. Theme constants: container 970, displayed = min(natural,940), offset = (970-displayed)/2, uniform s both axes.
- Proven accuracy: Y within 2.3px, X centers exact, 7/8 contract boxes at dx=0.0. Never re-scrape ch158 silently - use refetch (preserves translations).
- VERIFIED MODEL (do not "fix" without live proof): `x_offset=(970-displayed)/2` ALWAYS applied; `detect_left_mode` boundary uses rendered_w/2 (verified dx=0.0 on samples — leave the detector alone). H0 clamp [0,600] EVERYWHERE (incl. fallbacks); geo_tops deduped against page_texts (no self-corroboration); `calibrate_scale_from_tops` range [0.3,3.0].
- PITCH (reverted): per-page width-derived pitch moved ALL texts on real
  chapters — the live-verified sv model stays truth. Scrape logs
  `sv vs width-derived` each run; drift gets fixed with real numbers only.
  Worker fit == manual fit: same function, same export prefs, normalize+backup first.

## 8) عقد المهام (الخلفية مرئية دائماً)
- مهام الطابور بعد إعادة التشغيل تبدأ **PAUSED** (لا تشغيل ذاتي أبداً)؛
  الاستئناف عبر `POST /api/queue/resume/{job}` أو `/api/batch/{id}/resume`.
- السكراب المفرد قابل للإلغاء (`POST /api/scrape/cancel/{task}` + نقاط
  فحص بين المراحل)؛ الإلغاء يتخلى عن النتائج ولا يقتل الخيط بعنف.
- درج المهام (`#tasks-drawer` + شارة `#tasks-badge`) يعرض كل نشط دائماً
  خارج المودالات: إلغاء/استئناف/إعادة إرفاق. لا تُخفِ حالة خلفية أبداً.
