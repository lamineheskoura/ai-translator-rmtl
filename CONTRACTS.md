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

## 3) عقد المقاسات (shrink-only دائماً)
- الموقع/السكرابر يحدد الحجم؛ المحرر والمصدّر **يصغّران فقط** عند الفيض.
- التكبير مسموح فقط بزر "ضبط ذكي" اليدوي (allowGrow) — لا تلقائياً أبداً.
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
