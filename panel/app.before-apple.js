(function () {
  const state = {
    allRows: [],
    filtered: [],
    sortKey: 'value',
    sortDir: 'desc',
    page: 1,
    pageSize: 20,
    quotaType: '按量计费',
    selectedSites: new Set(),
    selectedModels: new Set(),
    maxAmount: null,
  };

  // ── 数据加载 ──────────────────────────────────────────

  async function loadData() {
    const base = location.pathname.replace(/\/panel\/.*$/, '/');
    const siteListRes = await fetch(base + 'site/');
    let siteDirs = [];
    let rechargeData = {};

    if (siteListRes.ok) {
      const html = await siteListRes.text();
      // 目录链接以 / 结尾
      const dirLinks = [...html.matchAll(/href="([^"]+)\/"/g)].map(m => decodeURIComponent(m[1]));
      siteDirs = dirLinks.filter(n => !n.startsWith('.'));
      // grouped-by-site JSON 文件链接
      const jsonLinks = [...html.matchAll(/href="([^"]*grouped-by-site[^"]*)"/g)].map(m => decodeURIComponent(m[1]));
      if (jsonLinks.length > 0) {
        const latestFile = jsonLinks.sort().pop();
        const res = await fetch(base + 'site/' + encodeURIComponent(latestFile));
        if (res.ok) rechargeData = await res.json();
      }
    }

    // 加载各站点的模型分组数据
    const modelData = {};
    for (const dir of siteDirs) {
      const encoded = encodeURIComponent(dir);
      const jsonPath = base + 'site/' + encoded + '/' + encoded + '.json';
      try {
        const res = await fetch(jsonPath);
        if (res.ok) modelData[dir] = await res.json();
      } catch (_) { /* 忽略加载失败的站点 */ }
    }

    // 生成笛卡尔积
    const rows = [];
    for (const [siteName, recharges] of Object.entries(rechargeData)) {
      const models = modelData[siteName];
      if (!models || !recharges) continue;
      for (const r of recharges) {
        for (const m of models) {
          const comboRatio = r['倍率'] * m.model_ratio * m.group_ratio;
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

  // ── 格式化 ──────────────────────────────────────────

  function fmt(n) {
    return Number(n).toFixed(2);
  }

  // ── 筛选 ──────────────────────────────────────────

  function applyFilters() {
    let data = state.allRows;
    data = data.filter(r => r.quotaType === state.quotaType);
    if (state.selectedSites.size > 0) {
      data = data.filter(r => state.selectedSites.has(r.site));
    }
    if (state.selectedModels.size > 0) {
      data = data.filter(r => state.selectedModels.has(r.model));
    }
    if (state.maxAmount !== null) {
      data = data.filter(r => r.amount <= state.maxAmount);
    }
    state.filtered = data;
    applySort();
  }

  // ── 排序 ──────────────────────────────────────────

  function applySort() {
    const key = state.sortKey;
    const dir = state.sortDir === 'asc' ? 1 : -1;
    state.filtered.sort((a, b) => {
      let va = a[key], vb = b[key];
      if (typeof va === 'string') {
        return va.localeCompare(vb) * dir;
      }
      return ((va || 0) - (vb || 0)) * dir;
    });
    state.page = 1;
    render();
  }

  // ── 渲染 ──────────────────────────────────────────

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
      tr.className = 'bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-750';

      // 1元相当
      const tdValue = cell(fmt(row.value));
      tdValue.classList.add('font-semibold', 'text-indigo-600', 'dark:text-indigo-400');
      tr.appendChild(tdValue);

      // 站点
      tr.appendChild(cell(row.site));

      // 充值/套餐
      const planText = row.planType === '充值' ? '充值' : (row.planName || '套餐');
      const tdPlan = cell(planText);
      if (row.planType === '充值') {
        tdPlan.firstChild.classList?.add?.('text-green-600', 'dark:text-green-400');
      } else {
        tdPlan.firstChild?.classList?.add?.('text-blue-600', 'dark:text-blue-400');
      }
      tr.appendChild(tdPlan);

      // 金额
      const amountHtml = `
        <div class="tooltip-trigger">
          <div>¥${fmt(row.amount)}</div>
          <div class="text-xs text-gray-400 dark:text-gray-500">倍率 ${fmt(row.preRatio)}</div>
          <div class="tooltip-content bg-gray-800 dark:bg-gray-200 text-white dark:text-gray-800 shadow-lg">
            额度: ${fmt(row.quota)} | 实际额度: ${fmt(row.realQuota)}${row.discount != null ? ' | 会员折扣: ' + row.discount : ''}
          </div>
        </div>`;
      const tdAmount = document.createElement('td');
      tdAmount.className = 'px-4 py-2';
      tdAmount.innerHTML = amountHtml;
      tr.appendChild(tdAmount);

      // 模型
      const modelHtml = `
        <div>
          <div>${escHtml(row.model)}</div>
          <div class="text-xs text-gray-400 dark:text-gray-500">倍率 ${fmt(row.modelRatio)}</div>
        </div>`;
      const tdModel = document.createElement('td');
      tdModel.className = 'px-4 py-2';
      tdModel.innerHTML = modelHtml;
      tr.appendChild(tdModel);

      // 分组
      const groupHtml = `
        <div class="tooltip-trigger">
          <div>${escHtml(row.group)}</div>
          <div class="text-xs text-gray-400 dark:text-gray-500">倍率 ${fmt(row.groupRatio)}</div>
          ${row.groupDesc ? `<div class="tooltip-content bg-gray-800 dark:bg-gray-200 text-white dark:text-gray-800 shadow-lg max-w-xs whitespace-normal">${escHtml(row.groupDesc)}</div>` : ''}
        </div>`;
      const tdGroup = document.createElement('td');
      tdGroup.className = 'px-4 py-2';
      tdGroup.innerHTML = groupHtml;
      tr.appendChild(tdGroup);

      // 计费方式
      tr.appendChild(cell(row.quotaType));

      tbody.appendChild(tr);
    }

    // 分页状态
    document.getElementById('totalCount').textContent = total;
    document.getElementById('curPage').textContent = curPage;
    document.getElementById('totalPages').textContent = totalPages;
    document.getElementById('prevPage').disabled = curPage <= 1;
    document.getElementById('nextPage').disabled = curPage >= totalPages;
  }

  function cell(text) {
    const td = document.createElement('td');
    td.className = 'px-4 py-2 whitespace-nowrap';
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

  // ── 筛选 UI 初始化 ──────────────────────────────────

  function initFilterDropdown(btnId, dropdownId, wrapId, searchId, selectAllId, clearAllId, optionsId, labelId, items, selectedSet, onChange) {
    const btn = document.getElementById(btnId);
    const dropdown = document.getElementById(dropdownId);
    const searchInput = document.getElementById(searchId);
    const optionsContainer = document.getElementById(optionsId);
    const label = document.getElementById(labelId);

    // 初始全选
    items.forEach(item => selectedSet.add(item));

    function renderOptions(filter) {
      optionsContainer.innerHTML = '';
      const filtered = filter ? items.filter(i => i.toLowerCase().includes(filter.toLowerCase())) : items;
      for (const item of filtered) {
        const div = document.createElement('div');
        div.className = 'filter-option';
        const lbl = document.createElement('label');
        lbl.className = 'text-sm text-gray-700 dark:text-gray-300';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = selectedSet.has(item);
        cb.className = 'rounded border-gray-300 dark:border-gray-600';
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

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('[id$="Dropdown"]').forEach(d => {
        if (d !== dropdown) d.classList.add('hidden');
      });
      dropdown.classList.toggle('hidden');
      if (!dropdown.classList.contains('hidden')) {
        searchInput.value = '';
        renderOptions('');
        searchInput.focus();
      }
    });

    searchInput.addEventListener('input', () => renderOptions(searchInput.value));

    document.getElementById(selectAllId).addEventListener('click', () => {
      items.forEach(i => selectedSet.add(i));
      renderOptions(searchInput.value);
      updateLabel();
      onChange();
    });

    document.getElementById(clearAllId).addEventListener('click', () => {
      selectedSet.clear();
      renderOptions(searchInput.value);
      updateLabel();
      onChange();
    });

    dropdown.addEventListener('click', e => e.stopPropagation());

    updateLabel();
    return renderOptions;
  }

  // ── 表头排序 ──────────────────────────────────────────

  function initSortHeaders() {
    document.querySelectorAll('#tableHead th[data-key]').forEach(th => {
      th.addEventListener('click', () => {
        const key = th.dataset.key;
        if (state.sortKey === key) {
          state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          state.sortKey = key;
          state.sortDir = key === 'value' ? 'desc' : 'asc';
        }
        updateSortArrows();
        applySort();
      });
    });
    updateSortArrows();
  }

  function updateSortArrows() {
    document.querySelectorAll('#tableHead th[data-key]').forEach(th => {
      const arrow = th.querySelector('.sort-arrow');
      if (th.dataset.key === state.sortKey) {
        arrow.textContent = state.sortDir === 'asc' ? '▲' : '▼';
      } else {
        arrow.textContent = '';
      }
    });
  }

  // ── 深浅模式 ──────────────────────────────────────────

  function initDarkMode() {
    const toggle = document.getElementById('darkToggle');
    const label = document.getElementById('darkLabel');
    const saved = localStorage.getItem('darkMode');
    let isDark;
    if (saved !== null) {
      isDark = saved === 'true';
    } else {
      isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    }
    applyDark(isDark);
    toggle.checked = isDark;

    toggle.addEventListener('change', () => {
      const dark = toggle.checked;
      applyDark(dark);
      localStorage.setItem('darkMode', dark);
    });

    function applyDark(dark) {
      document.documentElement.classList.toggle('dark', dark);
      label.textContent = dark ? '☀️' : '🌙';
    }
  }

  // ── sticky 表头偏移 ──────────────────────────────────

  function updateStickyOffset() {
    const nav = document.getElementById('navbar');
    const thead = document.querySelector('#tableHead');
    if (nav && thead) {
      thead.style.top = nav.offsetHeight + 'px';
    }
  }

  // ── 初始化 ──────────────────────────────────────────

  async function init() {
    initDarkMode();

    try {
      state.allRows = await loadData();
    } catch (err) {
      document.getElementById('loading').innerHTML =
        `<div class="text-red-500">数据加载失败: ${escHtml(err.message)}</div>`;
      return;
    }

    document.getElementById('loading').classList.add('hidden');
    document.getElementById('tableWrap').classList.remove('hidden');

    const allSites = [...new Set(state.allRows.map(r => r.site))].sort();
    const allModels = [...new Set(state.allRows.map(r => r.model))].sort();

    // 初始化筛选下拉
    initFilterDropdown('siteFilterBtn', 'siteDropdown', 'siteFilterWrap', 'siteSearch', 'siteSelectAll', 'siteClearAll', 'siteOptions', 'siteFilterLabel', allSites, state.selectedSites, applyFilters);
    initFilterDropdown('modelFilterBtn', 'modelDropdown', 'modelFilterWrap', 'modelSearch', 'modelSelectAll', 'modelClearAll', 'modelOptions', 'modelFilterLabel', allModels, state.selectedModels, applyFilters);

    // 点击外部关闭下拉
    document.addEventListener('click', () => {
      document.querySelectorAll('[id$="Dropdown"]').forEach(d => d.classList.add('hidden'));
    });

    // 计费模式切换
    document.querySelectorAll('.quota-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        state.quotaType = btn.dataset.value;
        document.querySelectorAll('.quota-btn').forEach(b => {
          b.classList.remove('bg-indigo-500', 'text-white');
          b.classList.add('bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
        });
        btn.classList.remove('bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
        btn.classList.add('bg-indigo-500', 'text-white');
        applyFilters();
      });
    });

    // 消费金额筛选
    document.getElementById('maxAmountBtn').addEventListener('click', () => {
      const val = document.getElementById('maxAmount').value.trim();
      state.maxAmount = val === '' ? null : Number(val);
      applyFilters();
    });
    document.getElementById('maxAmount').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') document.getElementById('maxAmountBtn').click();
    });

    // 分页
    document.getElementById('pageSize').addEventListener('change', (e) => {
      state.pageSize = Number(e.target.value);
      state.page = 1;
      render();
    });
    document.getElementById('prevPage').addEventListener('click', () => {
      if (state.page > 1) { state.page--; render(); }
    });
    document.getElementById('nextPage').addEventListener('click', () => {
      const totalPages = Math.ceil(state.filtered.length / state.pageSize);
      if (state.page < totalPages) { state.page++; render(); }
    });

    initSortHeaders();
    updateStickyOffset();
    window.addEventListener('resize', updateStickyOffset);

    applyFilters();
  }

  init();
})();
