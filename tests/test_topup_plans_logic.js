const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

function derivePlanMetrics(record) {
  const amount = Number(record['金额']);
  const quota = Number(record['额度']);
  const discount = record['会员折扣'];
  const hasDiscount = discount !== undefined && discount !== null && discount !== '';
  const numericDiscount = hasDiscount ? Number(discount) : null;
  const realQuota = hasDiscount ? quota / numericDiscount : quota;
  const preRatio = amount / realQuota;
  const tooltip = hasDiscount
    ? `额度: ${quota.toFixed(2)} | 会员折扣: ${numericDiscount.toFixed(2)} | 实际额度: ${realQuota.toFixed(2)}`
    : `额度: ${quota.toFixed(2)}`;

  return { amount, quota, discount, realQuota, preRatio, tooltip };
}

{
  const row = derivePlanMetrics({
    '金额': 89,
    '额度': 150,
    '会员折扣': null,
  });

  assert.equal(row.realQuota, 150);
  assert.equal(row.preRatio, 89 / 150);
  assert.equal(row.tooltip, '额度: 150.00');
}

{
  const row = derivePlanMetrics({
    '金额': 49.8,
    '额度': 50,
    '会员折扣': 0.85,
  });

  assert.equal(row.realQuota, 50 / 0.85);
  assert.equal(row.preRatio, 49.8 / (50 / 0.85));
  assert.equal(row.tooltip, '额度: 50.00 | 会员折扣: 0.85 | 实际额度: 58.82');
}

{
  const appSource = fs.readFileSync(path.join(__dirname, '..', 'panel', 'app.js'), 'utf8');

  assert.match(appSource, /const topupPath = base \+ 'site\/' \+ encoded \+ '\/' \+ TOPUP_PLANS_NAME;/);
  assert.match(appSource, /fetch\(topupPath\)/);
  assert.doesNotMatch(appSource, /lowerName\.includes\('topup'\) && lowerName\.includes\('plans'\)/);
}

console.log('topup+plans derived metrics assertions passed');
