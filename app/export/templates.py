"""HTML/CSS/JS template strings for report export."""

CSS_TEMPLATE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Meiryo', sans-serif;
  font-size: 14px; background: #f5f5f5; color: #333;
  padding-bottom: 60px;
}
header {
  position: sticky; top: 0; background: #fff;
  padding: 12px 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  z-index: 100;
}
header h1 { font-size: 18px; margin-bottom: 4px; }
.meta { font-size: 12px; color: #666; margin-bottom: 8px; }
.filters {
  display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
}
.filters input, .filters select {
  padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px;
  font-size: 14px;
}
.filters input { flex: 1; min-width: 150px; max-width: 300px; }
main { padding: 12px 16px; }

/* Ranking table */
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table {
  width: 100%; border-collapse: collapse; background: #fff;
  font-size: 13px; white-space: nowrap;
}
th, td { padding: 6px 10px; border-bottom: 1px solid #e0e0e0; text-align: left; }
th {
  background: #37474F; color: #fff; position: sticky; top: 0;
  cursor: pointer; user-select: none;
}
th:hover { background: #455A64; }
th .sort-arrow { font-size: 10px; margin-left: 4px; opacity: 0.5; }
th.sorted-asc .sort-arrow::after { content: '▲'; opacity: 1; }
th.sorted-desc .sort-arrow::after { content: '▼'; opacity: 1; }
tr:hover { background: #E3F2FD; }
tr.status-active td:first-child { border-left: 3px solid #4CAF50; }
tr.status-dead td:first-child { border-left: 3px solid #9E9E9E; }
tr.status-culled td:first-child { border-left: 3px solid #FF9800; }
tr.status-inactive td:first-child { border-left: 3px solid #FF9800; }

/* Score coloring */
.score-pos { color: #2E7D32; font-weight: bold; }
.score-neg { color: #C62828; }

/* Pedigree cards */
.card {
  background: #fff; border-radius: 8px; margin: 12px 0;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  overflow: hidden;
}
.card-header {
  padding: 10px 14px; background: #37474F; color: #fff;
  cursor: pointer; display: flex; justify-content: space-between;
  align-items: center; font-size: 14px;
}
.card-header:hover { background: #455A64; }
.card-header .toggle { font-size: 12px; opacity: 0.7; }
.card-body {
  display: none; padding: 12px 14px;
}
.card-body.open { display: block; }
.svg-container {
  overflow-x: auto; -webkit-overflow-scrolling: touch;
  padding: 8px 0;
}
.card-info {
  font-size: 12px; color: #555; margin-bottom: 8px;
}
.card-info span { margin-right: 12px; }

/* Back to top */
.top-btn {
  position: fixed; bottom: 20px; right: 20px;
  width: 44px; height: 44px; border-radius: 50%;
  background: #37474F; color: #fff; border: none;
  font-size: 20px; cursor: pointer; display: none;
  box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  z-index: 200;
}

@media (max-width: 768px) {
  header h1 { font-size: 15px; }
  table { font-size: 11px; }
  th, td { padding: 4px 6px; }
  .card-header { font-size: 13px; }
  .card-info { font-size: 11px; }
}
"""

JS_TEMPLATE = """
(function() {
  const searchInput = document.getElementById('search');
  const statusFilter = document.getElementById('status-filter');
  const table = document.getElementById('ranking-table');
  const tbody = table.querySelector('tbody');
  const cards = document.querySelectorAll('.card');
  const topBtn = document.getElementById('top-btn');
  const rows = Array.from(tbody.querySelectorAll('tr'));

  // --- Search & Filter ---
  function applyFilters() {
    const q = searchInput.value.trim().toUpperCase();
    const st = statusFilter.value;
    rows.forEach(tr => {
      const id = tr.dataset.id || '';
      const status = tr.dataset.status || '';
      const matchQ = !q || id.toUpperCase().includes(q);
      const matchS = st === 'all' || status === st;
      tr.style.display = (matchQ && matchS) ? '' : 'none';
    });
    cards.forEach(card => {
      const id = card.dataset.id || '';
      const status = card.dataset.status || '';
      const matchQ = !q || id.toUpperCase().includes(q);
      const matchS = st === 'all' || status === st;
      card.style.display = (matchQ && matchS) ? '' : 'none';
    });
  }
  searchInput.addEventListener('input', applyFilters);
  statusFilter.addEventListener('change', applyFilters);

  // --- Table Sort ---
  let sortCol = -1, sortAsc = true;
  table.querySelectorAll('th').forEach((th, idx) => {
    th.addEventListener('click', () => {
      if (sortCol === idx) { sortAsc = !sortAsc; }
      else { sortCol = idx; sortAsc = true; }
      table.querySelectorAll('th').forEach(h => {
        h.classList.remove('sorted-asc', 'sorted-desc');
      });
      th.classList.add(sortAsc ? 'sorted-asc' : 'sorted-desc');
      rows.sort((a, b) => {
        const av = a.children[idx]?.textContent || '';
        const bv = b.children[idx]?.textContent || '';
        const an = parseFloat(av), bn = parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn)) return sortAsc ? an - bn : bn - an;
        return sortAsc ? av.localeCompare(bv, 'ja') : bv.localeCompare(av, 'ja');
      });
      rows.forEach(r => tbody.appendChild(r));
    });
  });

  // --- Card Toggle ---
  document.querySelectorAll('.card-header').forEach(h => {
    h.addEventListener('click', () => {
      const body = h.nextElementSibling;
      body.classList.toggle('open');
      h.querySelector('.toggle').textContent =
        body.classList.contains('open') ? '▲ 閉じる' : '▼ 開く';
    });
  });

  // --- Back to Top ---
  window.addEventListener('scroll', () => {
    topBtn.style.display = window.scrollY > 400 ? 'block' : 'none';
  });
  topBtn.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
})();
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>母豚成績レポート - {report_date}</title>
<style>
{css}
</style>
</head>
<body>
<header>
  <h1>母豚成績レポート</h1>
  <p class="meta">生成日: {report_date} | 全母豚: {total_sows}頭 | 稼働: {active_sows}頭</p>
  <div class="filters">
    <input type="text" id="search" placeholder="個体番号で検索...">
    <select id="status-filter">
      <option value="all">全頭</option>
      <option value="active">稼働のみ</option>
      <option value="dead">死亡</option>
      <option value="culled">廃豚</option>
    </select>
  </div>
</header>
<main>
  <h2 style="margin:12px 0 8px;font-size:16px">順位表</h2>
  <div class="table-wrap">
  <table id="ranking-table">
    <thead>
      <tr>
        <th>全頭順位<span class="sort-arrow"></span></th>
        <th>稼働順位<span class="sort-arrow"></span></th>
        <th>個体番号<span class="sort-arrow"></span></th>
        <th>ステータス<span class="sort-arrow"></span></th>
        <th>産歴数<span class="sort-arrow"></span></th>
        <th>総合スコア<span class="sort-arrow"></span></th>
        <th>Peak<span class="sort-arrow"></span></th>
        <th>Stability<span class="sort-arrow"></span></th>
        <th>Sustain<span class="sort-arrow"></span></th>
        <th>OQ<span class="sort-arrow"></span></th>
        <th>母番号<span class="sort-arrow"></span></th>
        <th>父番号<span class="sort-arrow"></span></th>
      </tr>
    </thead>
    <tbody>
{table_rows}
    </tbody>
  </table>
  </div>

  <h2 style="margin:20px 0 8px;font-size:16px">母系家系図</h2>
{pedigree_cards}
</main>
<button id="top-btn" class="top-btn" title="トップへ">↑</button>
<script>
{js}
</script>
</body>
</html>
"""
