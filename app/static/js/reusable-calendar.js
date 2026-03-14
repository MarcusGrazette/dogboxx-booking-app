/**
 * Reusable Calendar Component
 * 
 * createCalendar(calendarId, callbacks) — returns calendar instance
 * getCalendar(calendarId) — retrieve existing instance
 */

const _calendarInstances = {};

function createCalendar(calendarId, callbacks = {}) {
  const wrapper = document.querySelector(`[data-calendar-id="${calendarId}"]`);
  if (!wrapper) { console.error('Calendar not found:', calendarId); return null; }

  const container = wrapper.querySelector('.calendar-container');
  const loadingEl = wrapper.querySelector('.calendar-loading');
  const titleEl = wrapper.querySelector('.calendar-title');
  const prevBtn = wrapper.querySelector('.calendar-prev-month');
  const nextBtn = wrapper.querySelector('.calendar-next-month');

  // Parse inline config
  let config = {};
  try { config = JSON.parse(wrapper.querySelector('.calendar-config')?.textContent || '{}'); } catch(e) {}

  const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  const DAYS = ['M','T','W','T','F','S','S'];

  const now = new Date();
  let currentYear = now.getFullYear();
  let currentMonth = now.getMonth() + 1; // 1-indexed
  let selectedDateStr = null;
  let highlightedDates = {};

  function fmt(y, m, d) {
    return `${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
  }

  function render() {
    const todayStr = fmt(now.getFullYear(), now.getMonth() + 1, now.getDate());
    if (titleEl) titleEl.textContent = `${MONTHS[currentMonth - 1]} ${currentYear}`;

    // First weekday of month (convert Sun=0 to Mon-based: Mon=0..Sun=6)
    const firstDow = new Date(currentYear, currentMonth - 1, 1).getDay();
    const offset = (firstDow + 6) % 7;
    const daysInMonth = new Date(currentYear, currentMonth, 0).getDate();

    let html = '<table class="calendar-table"><thead><tr>';
    DAYS.forEach(d => html += `<th>${d}</th>`);
    html += '</tr></thead><tbody>';

    let day = 1 - offset;
    for (let row = 0; row < 6; row++) {
      if (day > daysInMonth) break;
      html += '<tr>';
      for (let col = 0; col < 7; col++, day++) {
        if (day < 1 || day > daysInMonth) {
          html += '<td class="cal-cell cal-empty"></td>';
        } else {
          const ds = fmt(currentYear, currentMonth, day);
          const cls = ['cal-cell', 'cal-day'];
          if (ds === todayStr) cls.push('cal-today');
          if (ds === selectedDateStr) cls.push('cal-selected');
          if (col >= 5) cls.push('cal-weekend');
          const hl = highlightedDates[ds];
          if (hl) {
            // String value ('pending', 'confirmed') → cal-<status>
            // Truthy object value (admin compat) → cal-pending
            cls.push(typeof hl === 'string' ? 'cal-' + hl : 'cal-pending');
          }
          html += `<td class="${cls.join(' ')}" data-date="${ds}" data-day="${day}">${day}</td>`;
        }
      }
      html += '</tr>';
    }
    html += '</tbody></table>';
    container.innerHTML = html;

    // Click handlers
    container.querySelectorAll('.cal-day').forEach(cell => {
      cell.addEventListener('click', () => {
        const d = parseInt(cell.dataset.day);
        selectedDateStr = cell.dataset.date;
        render();
        if (callbacks.onDateClick) callbacks.onDateClick(currentYear, currentMonth, d, selectedDateStr);
        if (callbacks.onDateSelect) callbacks.onDateSelect(currentYear, currentMonth, d);
      });
    });
  }

  function navigate(delta) {
    currentMonth += delta;
    if (currentMonth > 12) { currentMonth = 1; currentYear++; }
    if (currentMonth < 1) { currentMonth = 12; currentYear--; }
    selectedDateStr = null;
    highlightedDates = {};
    render();
    if (callbacks.onMonthChange) callbacks.onMonthChange(currentYear, currentMonth);
  }

  if (prevBtn) prevBtn.addEventListener('click', () => navigate(-1));
  if (nextBtn) nextBtn.addEventListener('click', () => navigate(1));

  // Public API
  const instance = {
    get year() { return currentYear; },
    get month() { return currentMonth; },
    get selectedDate() { return selectedDateStr; },
    setHighlightedDates(dates) { highlightedDates = dates || {}; render(); if (loadingEl) loadingEl.style.display = 'none'; },
    showLoading() { if (loadingEl) loadingEl.style.display = 'flex'; },
    hideLoading() { if (loadingEl) loadingEl.style.display = 'none'; },
    selectDate(dateStr) { selectedDateStr = dateStr; render(); },
    render
  };

  _calendarInstances[calendarId] = instance;

  // Initial render — hide loading immediately, render calendar
  render();
  if (loadingEl) loadingEl.style.display = 'none';

  // Auto-select today if configured
  if (config.autoSelectToday) {
    const todayStr = fmt(now.getFullYear(), now.getMonth() + 1, now.getDate());
    if (todayStr.startsWith(`${currentYear}-${String(currentMonth).padStart(2,'0')}`)) {
      selectedDateStr = todayStr;
      render();
      if (callbacks.onDateSelect) callbacks.onDateSelect(currentYear, currentMonth, now.getDate());
    }
  }

  // Fire initial month change to load data
  if (callbacks.onMonthChange) callbacks.onMonthChange(currentYear, currentMonth);

  return instance;
}

function getCalendar(calendarId) {
  return _calendarInstances[calendarId] || null;
}
