const $ = s => document.querySelector(s);
let selected = 'best',
    jobId = null;

$('#inspect').onclick = async () => {
    const url = $('#url').value.trim();
    if (!url) return;
    $('#inspect').disabled = true;
    $('#inspect').textContent = 'جارٍ الفحص…';
    try {
        const r = await fetch('/api/inspect', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ url })
        });
        const d = await r.json();
        if (!r.ok) throw Error(d.detail || 'تعذر الفحص');
        $('#result').classList.remove('hidden');
        $('#title').textContent = d.title || 'فيديو';
        $('#thumb').src = d.thumbnail || '';
        $('#duration').textContent = d.duration ? `${Math.floor(d.duration / 60)} دقيقة` : 'المدة غير معروفة';
        const box = $('#formats');
        box.innerHTML = '';
        (d.formats || []).forEach((f, i) => {
            const b = document.createElement('button');
            b.className = 'format' + (i === 0 ? ' active' : '');
            // 📦 حجم مقروء: GB للملفات الكبيرة، MB للصغيرة
            const sizeStr = f.size
                ? (f.size >= 1073741824
                    ? ` · ${(f.size / 1073741824).toFixed(1)} GB`
                    : ` · ${(f.size / 1048576).toFixed(1)} MB`)
                : '';
            b.textContent = f.label + sizeStr;
            b.onclick = () => {
                document.querySelectorAll('.format').forEach(x => x.classList.remove('active'));
                b.classList.add('active');
                selected = f.id;
            };
            box.appendChild(b);
        });
        selected = d.formats?.[0]?.id || 'best';
        $('#download').disabled = false;
    } catch (e) {
        alert(e.message);
    } finally {
        $('#inspect').disabled = false;
        $('#inspect').textContent = 'فحص الرابط';
    }
};

$('#download').onclick = async () => {
    const r = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ url: $('#url').value.trim(), format_id: selected })
    });
    const d = await r.json();
    if (!r.ok) return alert(d.detail || 'تعذر بدء المعالجة');
    jobId = d.id;
    $('#download').disabled = true;
    $('#progress').classList.remove('hidden');
    poll();
};

async function poll() {
    const r = await fetch('/api/jobs/' + jobId),
        d = await r.json();
    const statusMap = {
        waiting: 'في الانتظار',
        downloading: 'جاري التحميل',
        preparing: 'جاري التجهيز',
        ready: 'جاهز',
        failed: 'فشل'
    };
    const statusText = statusMap[d.status] || d.status;
    // 📊 حجم الملف المقروء
    const sizeStr = d.file_size
        ? (d.file_size >= 1073741824
            ? `${(d.file_size / 1073741824).toFixed(1)} GB`
            : `${(d.file_size / 1048576).toFixed(1)} MB`)
        : '';
    // 📊 نسبة التقدم من status.json (إن توفرت)
    const pctInfo = d.pct !== undefined && d.pct > 0 ? ` — ${d.pct}%` : '';
    $('#status').textContent = statusText + (sizeStr ? ` · ${sizeStr}` : '') + pctInfo;
    if (d.status === 'ready') {
        $('#status').textContent = 'جاهز — جارٍ التنزيل…';
        location.href = d.download_url;
        return;
    }
    if (d.status === 'failed') {
        alert(d.error || 'فشلت المعالجة');
        $('#download').disabled = false;
        return;
    }
    setTimeout(poll, 2000);
}
