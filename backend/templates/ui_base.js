/* -*- coding: utf-8 -*-
 * مساعد مشترك لشاشات ERP (مجلد backend/templates/pages)
 * - قراءة التوكن من localStorage (يتشاركه كل التبويبات والنوافذ)
 * - api(): استدعاء JSON مع Authorization + إعادة توجيه تلقائية للدخول عند انتهائه
 * - حقل التاريخ/الأرقام بالعربية، وHTML safe escape
 */
const ERP = {
  API: '/api/v1',

  token() { return localStorage.getItem('erp_token') || localStorage.getItem('erp_access') || ''; },
  refresh() { return localStorage.getItem('erp_refresh') || ''; },

  setSession(d) {
    localStorage.setItem('erp_token', d.access_token || '');
    localStorage.setItem('erp_refresh', d.refresh_token || '');
    localStorage.setItem('erp_user', (d.user && d.user.username) || '');
  },

  logout() {
    ['erp_token', 'erp_access', 'erp_refresh', 'erp_user'].forEach(k => localStorage.removeItem(k));
    location.href = '/login';
  },

  user() {
    const raw = localStorage.getItem('erp_user') || '';
    try { const o = JSON.parse(raw); return (o && o.username) || raw; } catch (e) { return raw; }
  },

  async api(path, opts = {}) {
    const headers = Object.assign({'Content-Type': 'application/json'}, opts.headers || {});
    const tok = this.token();
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    let res = await fetch(this.API + path, Object.assign({headers}, opts));
    if (res.status === 401 && this.refresh()) {
      // محاولة تحديث الجلسة مرة واحدة ثم إعادة الطلب الأصلي
      const r = await fetch(this.API + '/auth/refresh', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({refresh_token: this.refresh()})
      });
      if (r.ok) {
        const j = await r.json();
        if (j.success) {
          localStorage.setItem('erp_token', j.data.access_token);
          headers['Authorization'] = 'Bearer ' + j.data.access_token;
          res = await fetch(this.API + path, Object.assign({headers}, opts));
        }
      } else {
        this.logout();
        throw new Error('انتهت الجلسة — سجّل الدخول من /login');
      }
    }
    const j = await res.json().catch(() => ({success: false, message: 'استجابة غير صالحة (' + res.status + ')'}));
    if (!j.success) throw new Error(j.message || ('خطأ ' + res.status));
    return j.data;
  },

  get(path)            { return this.api(path); },
  post(path, body)     { return this.api(path, {method: 'POST', body: JSON.stringify(body || {})}); },
  put(path, body)      { return this.api(path, {method: 'PUT', body: JSON.stringify(body || {})}); },

  esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  },

  money(v) {
    const n = Number(v || 0);
    return n.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  },

  today()  { return new Date().toISOString().slice(0, 10); },
  month()  { return new Date().toISOString().slice(0, 7); },

  toast(msg, ok = true) {
    const el = document.getElementById('toastBox');
    if (!el) return;
    el.className = 'alert ' + (ok ? 'alert-success' : 'alert-danger') + ' py-2 px-3 mb-3 shadow-sm';
    el.innerHTML = (ok ? '<i class="bi bi-check-circle"></i> ' : '<i class="bi bi-exclamation-triangle"></i> ') + this.esc(msg);
    el.style.display = 'block';
    clearTimeout(this._tt);
    this._tt = setTimeout(() => { el.style.display = 'none'; }, 6000);
  },

  loading(sel = '#content') {
    document.querySelector(sel).innerHTML =
      '<div class="text-center p-5"><div class="spinner-border text-primary"></div><div class="text-muted mt-2">جاري التحميل...</div></div>';
  }
};

// شريط الحالة أعلى الشاشة (اسم المستخدم + خروج + رجوع للرئيسية)
document.addEventListener('DOMContentLoaded', () => {
  const u = document.getElementById('whoami');
  if (u) u.innerHTML = ERP.user()
    ? `<span class="badge bg-light text-dark">👤 ${ERP.esc(ERP.user())}</span>
       <button class="btn btn-sm btn-outline-light ms-1" onclick="ERP.logout()">خروج</button>`
    : '<a class="btn btn-sm btn-warning" href="/login">🔑 سجّل الدخول</a>';
});

// خدمة تقديم ملفات المساعدة الثابتة تُفعَّل عند تشغيل التطبيق عبر:
// app.mount('/templates', StaticFiles(directory='backend/templates'))
