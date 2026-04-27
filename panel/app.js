(function () {
  const state = {
    allRows: [],
    filtered: [],
    sortKey: 'value',
    sortDir: 'desc',
    page: 1,
    pageSize: 10,
    quotaType: '按量计费',
    selectedSites: new Set(),
    selectedModels: new Set(),
    maxAmount: null,
  };

  async function loadData() {
    const base = location.pathname.replace(/\/panel\/.*$/, '/');
    const siteListRes = await fetch(base + 'site/');
    let siteDirs = [];
    let rechargeData = {};

    if (siteListRes.ok) {
      const html = await siteListRes.text();
      const dirLinks = [...html.matchAll(/href="([^"]+)\/"/g)].map(m => decodeURIComponent(m[1]));
      siteDirs = dirLinks.filter(n => !n.startsWith('.'));
      const jsonLinks = [...html.matchAll(/href="([^"]*grouped-by-site[^"]*)"/g)].map(m => decodeURIComponent(m[1]));
      if (jsonLinks.length > 0) {
        const latestFile = jsonLinks.sort().pop();
        const res = await fetch(base + 'site/' + encodeURIComponent(latestFile));
        if (res.ok) rechargeData = await res.json();
      }
    }

    const modelData = {};
    for (const dir of siteDirs) {
      const encoded = encodeURIComponent(dir);
      const jsonPath = base + 'site/' + encoded + '/' + encoded + '.json';
      try {
        const res = await fetch(jsonPath);
        if (res.ok) modelData[dir] = await res.json();
      } catch (_) {}
    }

    const rows = [];
    for (const [siteName, recharges] of Object.entries(rechargeData)) {
      const models = modelData[siteName];
      if (!models || !recharges) continue;
      for (const r of recharges) {
        for (const m of models) {
          const modelUnit = m.quota_type === '按次计费' ? m.model_price : m.model_ratio;
          const comboRatio = r['倍率'] * modelUnit * m.group_ratio;
          const valuePerYuan = comboRatio === 0 ? 0 : 1 / comboRatio;
          rows.push({
            value: valuePerYuan,
            site: siteName,
            planType: r['类型'],
            planName: r['套餐名'],
            amount: r['金额'],
            preRatio: r['倍率'],
            quota: r['额度'],
            discount: r['会员折扣'],
            realQuota: r['实际额度'],
            model: m.model_name,
            modelRatio: m.model_ratio,
            modelPrice: m.model_price,
            group: m.group_name,
            groupRatio: m.group_ratio,
            groupDesc: m.group_description || '',
            quotaType: m.quota_type,
            comboRatio: comboRatio,
          });
        }
      }
    }
    return rows;
  }

  function fmt(n) {
    return Number(n).toFixed(2);
  }

  function applyFilters() {
    let data = state.allRows;
    data = data.filter(r => r.quotaType === state.quotaType);
    data = data.filter(r => state.selectedSites.has(r.site));
    data = data.filter(r => state.selectedModels.has(r.model));
    if (state.maxAmount !== null) {
      data = data.filter(r => r.amount <= state.maxAmount);
    }
    state.filtered = data;
    applySort();
  }

  function applySort() {
    const key = state.sortKey;
    const dir = state.sortDir === 'asc' ? 1 : -1;
    state.filtered.sort((a, b) => {
      const va = a[key];
      const vb = b[key];
      if (typeof va === 'string') {
        return va.localeCompare(vb) * dir;
      }
      return ((va || 0) - (vb || 0)) * dir;
    });
    state.page = 1;
    render();
  }

  function render() {
    const { filtered, page, pageSize } = state;
    const total = filtered.length;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const curPage = Math.min(page, totalPages);
    state.page = curPage;

    const start = (curPage - 1) * pageSize;
    const slice = filtered.slice(start, start + pageSize);

    const tbody = document.getElementById('tableBody');
    tbody.innerHTML = '';

    for (const row of slice) {
      const tr = document.createElement('tr');

      const tdValue = cell(fmt(row.value), 'cell-highlight');
      tr.appendChild(tdValue);

      tr.appendChild(cell(row.site, 'cell-primary'));

      const planText = row.planType === '充值' ? '充值' : (row.planName || '套餐');
      const planClass = row.planType === '充值' ? 'plan-tag--recharge' : 'plan-tag--package';
      tr.appendChild(cell(planText, `cell-primary ${planClass}`));

      const amountHtml = `
        <div class="tooltip-trigger">
          <div class="cell-primary">¥${fmt(row.amount)}</div>
          <div class="cell-meta">倍率 ${fmt(row.preRatio)}</div>
          <div class="tooltip-content">
            额度: ${fmt(row.quota)} | 实际额度: ${fmt(row.realQuota)}${row.discount != null ? ' | 会员折扣: ' + row.discount : ''}
          </div>
        </div>`;
      const tdAmount = document.createElement('td');
      tdAmount.innerHTML = amountHtml;
      tr.appendChild(tdAmount);

      const modelMeta = row.quotaType === '按次计费' ? `价格 ${fmt(row.modelPrice)}` : `倍率 ${fmt(row.modelRatio)}`;
      const modelHtml = `
        <div>
          <div class="cell-primary">${escHtml(row.model)}</div>
          <div class="cell-meta">${modelMeta}</div>
        </div>`;
      const tdModel = document.createElement('td');
      tdModel.innerHTML = modelHtml;
      tr.appendChild(tdModel);

      const groupHtml = `
        <div class="tooltip-trigger">
          <div class="cell-primary">${escHtml(row.group)}</div>
          <div class="cell-meta">倍率 ${fmt(row.groupRatio)}</div>
          ${row.groupDesc ? `<div class="tooltip-content whitespace-normal">${escHtml(row.groupDesc)}</div>` : ''}
        </div>`;
      const tdGroup = document.createElement('td');
      tdGroup.innerHTML = groupHtml;
      tr.appendChild(tdGroup);

      tr.appendChild(cell(row.quotaType, 'cell-primary'));

      tbody.appendChild(tr);
    }

    document.getElementById('totalCount').textContent = total;
    document.getElementById('curPage').textContent = curPage;
    document.getElementById('totalPages').textContent = totalPages;
    document.getElementById('prevPage').disabled = curPage <= 1;
    document.getElementById('nextPage').disabled = curPage >= totalPages;
    scheduleStickyUpdate();
  }

  function cell(text, extraClass = '') {
    const td = document.createElement('td');
    if (extraClass) td.className = extraClass;
    const span = document.createElement('span');
    span.textContent = text;
    td.appendChild(span);
    return td;
  }

  function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  function initFilterDropdown(btnId, dropdownId, wrapId, searchId, selectAllId, clearAllId, optionsId, labelId, items, selectedSet, onChange) {
    const btn = document.getElementById(btnId);
    const dropdown = document.getElementById(dropdownId);
    const searchInput = document.getElementById(searchId);
    const optionsContainer = document.getElementById(optionsId);
    const label = document.getElementById(labelId);

    items.forEach(item => selectedSet.add(item));

    function getVisibleItems() {
      const filter = searchInput.value.trim().toLowerCase();
      return filter ? items.filter(i => i.toLowerCase().includes(filter)) : items;
    }

    function renderOptions() {
      optionsContainer.innerHTML = '';
      const filtered = getVisibleItems();
      for (const item of filtered) {
        const div = document.createElement('div');
        div.className = 'filter-option';
        const lbl = document.createElement('label');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = selectedSet.has(item);
        cb.addEventListener('change', () => {
          if (cb.checked) selectedSet.add(item);
          else selectedSet.delete(item);
          updateLabel();
          onChange();
        });
        const span = document.createElement('span');
        span.textContent = item;
        lbl.appendChild(cb);
        lbl.appendChild(span);
        div.appendChild(lbl);
        optionsContainer.appendChild(div);
      }
    }

    function updateLabel() {
      if (selectedSet.size === 0) label.textContent = '无';
      else if (selectedSet.size === items.length) label.textContent = '全部';
      else if (selectedSet.size <= 2) label.textContent = [...selectedSet].join(', ');
      else label.textContent = selectedSet.size + ' 项';
    }

    btn.addEventListener('click', e => {
      e.stopPropagation();
      document.querySelectorAll('[id$="Dropdown"]').forEach(d => {
        if (d !== dropdown) d.classList.add('hidden');
      });
      dropdown.classList.toggle('hidden');
      if (!dropdown.classList.contains('hidden')) {
        searchInput.value = '';
        renderOptions();
        searchInput.focus();
      }
    });

    searchInput.addEventListener('input', () => renderOptions());

    document.getElementById(selectAllId).addEventListener('click', () => {
      getVisibleItems().forEach(i => selectedSet.add(i));
      renderOptions();
      updateLabel();
      onChange();
    });

    document.getElementById(clearAllId).addEventListener('click', () => {
      selectedSet.clear();
      renderOptions();
      updateLabel();
      onChange();
    });

    dropdown.addEventListener('click', e => e.stopPropagation());

    updateLabel();
    return renderOptions;
  }

  function handleSortKey(key) {
    if (state.sortKey === key) {
      state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      state.sortKey = key;
      state.sortDir = key === 'value' ? 'desc' : 'asc';
    }
    updateSortArrows();
    applySort();
  }

  function initSortHeaders() {
    document.querySelectorAll('#tableHead th[data-key]').forEach(th => {
      th.addEventListener('click', () => handleSortKey(th.dataset.key));
    });
    updateSortArrows();
  }

  function updateSortArrows() {
    document.querySelectorAll('#tableHead th[data-key], #fixedTableHead th[data-key]').forEach(th => {
      const arrow = th.querySelector('.sort-arrow');
      if (th.dataset.key === state.sortKey) {
        arrow.textContent = state.sortDir === 'asc' ? '▲' : '▼';
      } else {
        arrow.textContent = '';
      }
    });
  }

  function initDarkMode() {
    const toggle = document.getElementById('darkToggle');
    const label = document.getElementById('darkLabel');
    const saved = localStorage.getItem('darkMode');
    const isDark = saved !== null ? saved === 'true' : true;

    applyDark(isDark);
    toggle.checked = isDark;

    toggle.addEventListener('change', () => {
      const dark = toggle.checked;
      applyDark(dark);
      localStorage.setItem('darkMode', String(dark));
    });

    function applyDark(dark) {
      document.documentElement.classList.toggle('dark', dark);
      label.textContent = dark ? '深色' : '浅色';
    }
  }

  function syncFixedHeader() {
    const thead = document.getElementById('tableHead');
    if (!thead || document.getElementById('fixedTableHead')) return;

    const fixedHead = document.createElement('div');
    fixedHead.id = 'fixedTableHead';
    fixedHead.className = 'fixed-table-head hidden';
    fixedHead.innerHTML = `<table class="panel-table text-left"><thead>${thead.innerHTML}</thead></table>`;
    fixedHead.querySelectorAll('th[data-key]').forEach(th => {
      th.addEventListener('click', () => handleSortKey(th.dataset.key));
    });
    document.body.appendChild(fixedHead);
    updateSortArrows();
  }

  function updateStickyOffset() {
    const nav = document.getElementById('navbar');
    const tableScroll = document.querySelector('.table-scroll');
    const table = tableScroll && tableScroll.querySelector('.panel-table');
    const thead = document.getElementById('tableHead');
    const fixedHead = document.getElementById('fixedTableHead');
    if (!nav || !tableScroll || !table || !thead || !fixedHead) return;

    const navRect = nav.getBoundingClientRect();
    const scrollRect = tableScroll.getBoundingClientRect();
    const tableRect = table.getBoundingClientRect();
    const headRect = thead.getBoundingClientRect();
    const headHeight = headRect.height;
    const shouldFix = headHeight > 0 && headRect.top < navRect.bottom && tableRect.bottom > navRect.bottom + headHeight;

    fixedHead.classList.toggle('hidden', !shouldFix);
    tableScroll.classList.toggle('is-fixed-head-clipped', shouldFix);
    if (!shouldFix) {
      tableScroll.style.removeProperty('--fixed-head-clip');
      return;
    }

    const clipTop = Math.max(0, navRect.bottom + headHeight - scrollRect.top);
    tableScroll.style.setProperty('--fixed-head-clip', clipTop + 'px');

    fixedHead.style.top = navRect.bottom + 'px';
    fixedHead.style.left = scrollRect.left + 'px';
    fixedHead.style.width = tableScroll.clientWidth + 'px';
    fixedHead.style.height = headHeight + 'px';
    fixedHead.scrollLeft = tableScroll.scrollLeft;

    const sourceHeaders = [...thead.querySelectorAll('th')];
    const fixedHeaders = [...fixedHead.querySelectorAll('th')];
    sourceHeaders.forEach((th, index) => {
      const width = th.getBoundingClientRect().width + 'px';
      if (fixedHeaders[index]) {
        fixedHeaders[index].style.width = width;
        fixedHeaders[index].style.minWidth = width;
        fixedHeaders[index].style.maxWidth = width;
      }
    });
  }

  function scheduleStickyUpdate() {
    requestAnimationFrame(updateStickyOffset);
  }

  function initQuotaToggle() {
    const buttons = [...document.querySelectorAll('.quota-btn')];

    function syncQuotaButtons() {
      buttons.forEach(btn => {
        btn.classList.toggle('is-active', btn.dataset.value === state.quotaType);
      });
    }

    buttons.forEach(btn => {
      btn.addEventListener('click', () => {
        state.quotaType = btn.dataset.value;
        syncQuotaButtons();
        applyFilters();
      });
    });

    syncQuotaButtons();
  }

  async function init() {
    initDarkMode();

    try {
      state.allRows = await loadData();
    } catch (err) {
      document.getElementById('loading').innerHTML =
        `<div style="color:#ef4444;">数据加载失败: ${escHtml(err.message)}</div>`;
      return;
    }

    document.getElementById('loading').classList.add('hidden');
    document.getElementById('tableWrap').classList.remove('hidden');

    const allSites = [...new Set(state.allRows.map(r => r.site))].sort();
    const allModels = [...new Set(state.allRows.map(r => r.model))].sort();

    initFilterDropdown('siteFilterBtn', 'siteDropdown', 'siteFilterWrap', 'siteSearch', 'siteSelectAll', 'siteClearAll', 'siteOptions', 'siteFilterLabel', allSites, state.selectedSites, applyFilters);
    initFilterDropdown('modelFilterBtn', 'modelDropdown', 'modelFilterWrap', 'modelSearch', 'modelSelectAll', 'modelClearAll', 'modelOptions', 'modelFilterLabel', allModels, state.selectedModels, applyFilters);

    document.addEventListener('click', () => {
      document.querySelectorAll('[id$="Dropdown"]').forEach(d => d.classList.add('hidden'));
    });

    initQuotaToggle();

    document.getElementById('maxAmountBtn').addEventListener('click', () => {
      const val = document.getElementById('maxAmount').value.trim();
      state.maxAmount = val === '' ? null : Number(val);
      applyFilters();
    });
    document.getElementById('maxAmount').addEventListener('keydown', e => {
      if (e.key === 'Enter') document.getElementById('maxAmountBtn').click();
    });

    document.getElementById('pageSize').addEventListener('change', e => {
      state.pageSize = Number(e.target.value);
      state.page = 1;
      render();
    });
    document.getElementById('prevPage').addEventListener('click', () => {
      if (state.page > 1) {
        state.page--;
        render();
      }
    });
    document.getElementById('nextPage').addEventListener('click', () => {
      const totalPages = Math.ceil(state.filtered.length / state.pageSize);
      if (state.page < totalPages) {
        state.page++;
        render();
      }
    });

    initSortHeaders();
    syncFixedHeader();
    scheduleStickyUpdate();
    window.addEventListener('resize', scheduleStickyUpdate);
    window.addEventListener('scroll', scheduleStickyUpdate, { passive: true });
    document.querySelector('.table-scroll').addEventListener('scroll', scheduleStickyUpdate, { passive: true });

    applyFilters();
  }

  init();
})();
