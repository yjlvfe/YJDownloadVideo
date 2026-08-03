const $ = (selector) => document.querySelector(selector);

let selected = 'best';
let jobId = null;
let pollTimer = null;

function setProgress(text, percent = 15) {
  const progress = $('#progress');
  const bar = progress.querySelector('.bar');
  progress.classList.remove('hidden');
  progress.querySelector('span').textContent = text;
  const safePercent = Math.max(4, Math.min(96, percent));
  bar.style.background = `linear-gradient(90deg, var(--blue) ${safePercent}%, #e5e7eb ${safePercent}%)`;
}

function resetProgress() {
  $('#progress').classList.add('hidden');
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

$('#inspect').onclick = async () => {
  const url = $('#url').value.trim();
  if (!url) return;

  $('#inspect').disabled = true;
  $('#inspect').textContent = 'جارٍ الفحص…';
  try {
    const response = await fetch('/api/inspect', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'تعذر الفحص');

    $('#result').classList.remove('hidden');
    $('#title').textContent = data.title || 'فيديو';
    $('#thumb').src = data.thumbnail || '';
    $('#duration').textContent = data.duration
      ? `${Math.floor(data.duration / 60)} دقيقة`
      : 'المدة غير معروفة';

    const formatsBox = $('#formats');
    formatsBox.innerHTML = '';
    (data.formats || []).forEach((format, index) => {
      const button = document.createElement('button');
      button.className = `format${index === 0 ? ' active' : ''}`;
      button.textContent = format.label + (format.size
        ? ` · ${(format.size / 1048576).toFixed(1)} MB`
        : '');
      button.onclick = () => {
        document.querySelectorAll('.format').forEach((item) => item.classList.remove('active'));
        button.classList.add('active');
        selected = format.id;
      };
      formatsBox.appendChild(button);
    });
    selected = data.formats?.[0]?.id || 'best';
    $('#download').disabled = false;
    resetProgress();
  } catch (error) {
    alert(error.message);
  } finally {
    $('#inspect').disabled = false;
    $('#inspect').textContent = 'فحص الرابط';
  }
};

async function downloadReadyFile(url, title = 'video') {
  let lastError = new Error('الملف غير متاح');
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    setProgress(`الملف جاهز، جارٍ بدء التنزيل… (${attempt}/4)`, 96);
    try {
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) throw new Error(`تعذر جلب الملف (${response.status})`);
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = `${String(title).replace(/[\\/:*?"<>|]/g, '_').slice(0, 100) || 'video'}.mp4`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
      resetProgress();
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 500 * attempt));
    }
  }
  throw lastError;
}

$('#download').onclick = async () => {
  $('#download').disabled = true;
  let elapsed = 0;
  setProgress('جارٍ تنزيل الفيديو من المصدر…', 12);
  const clock = setInterval(() => {
    elapsed += 1;
    setProgress(`جارٍ تنزيل وتجهيز الملف… ${elapsed} ثانية`, Math.min(88, 12 + elapsed * 2));
  }, 1000);

  try {
    const response = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ url: $('#url').value.trim(), format_id: selected }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'تعذر بدء المعالجة');

    jobId = data.id;
    clearInterval(clock);

    if (data.status === 'ready' && data.download_url) {
      await downloadReadyFile(data.download_url, data.title || $('#title').textContent);
      $('#download').disabled = false;
      return;
    }
    if (data.status === 'failed') {
      throw new Error(data.error || 'فشلت المعالجة');
    }

    setProgress('جاري إكمال المعالجة…', 75);
    poll();
  } catch (error) {
    clearInterval(clock);
    resetProgress();
    $('#download').disabled = false;
    alert(error.message);
  }
};

async function poll() {
  try {
    const response = await fetch(`/api/jobs/${jobId}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'تعذر قراءة حالة العملية');

    const statusText = {
      waiting: 'في الانتظار',
      downloading: 'جاري التحميل',
      preparing: 'جاري التجهيز',
      ready: 'جاهز',
      failed: 'فشل',
    }[data.status] || data.status;
    $('#status').textContent = statusText;

    if (data.status === 'ready') {
      await downloadReadyFile(data.download_url, data.title || $('#title').textContent);
      $('#download').disabled = false;
      return;
    }
    if (data.status === 'failed') {
      throw new Error(data.error || 'فشلت المعالجة');
    }

    setProgress(data.status === 'preparing' ? 'جارٍ دمج الصوت والفيديو…' : 'جارٍ تنزيل الملف…', data.status === 'preparing' ? 88 : 70);
    pollTimer = setTimeout(poll, 1500);
  } catch (error) {
    resetProgress();
    $('#download').disabled = false;
    alert(error.message);
  }
}
