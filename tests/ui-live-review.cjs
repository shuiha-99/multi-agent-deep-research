/* Read-only live-result review in a fresh headless browser. No model calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const playwright = require(process.env.PLAYWRIGHT_LIBRARY_PATH || 'playwright');

const root = path.resolve(__dirname, '..');
const parsed = new URL(process.env.UI_LIVE_BASE_URL || 'http://127.0.0.1:8000');
assert.equal(parsed.protocol, 'http:');
assert(['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname));
assert(!parsed.username && !parsed.password);
const base = parsed.origin;
const output = path.join(root, 'artifacts', 'ui-live');
const temporary = path.join(root, '.cache', 'ui-live');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(temporary, { recursive: true });
const report = { started_at: new Date().toISOString(), base, read_only: true, isolated_browser: true, checks: [], screenshots: [], page_errors: [], console_errors: [], blocked_remote_requests: [] };
let browser, context, page;

function checked(name, extra = {}) {
  report.checks.push({ name, passed: true, ...extra });
  process.stdout.write(`PASS ${name}\n`);
}
async function screenshot(name) {
  const target = path.join(output, `${name}.png`);
  await page.screenshot({ path: target, fullPage: false });
  report.screenshots.push(path.relative(root, target).replaceAll('\\', '/'));
}
async function nav(name) {
  const mobile = await page.getByRole('button', { name: '打开导航', exact: true }).isVisible();
  if (mobile) await page.getByRole('button', { name: '打开导航', exact: true }).click();
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name }).click();
}
async function noOverflow(name) {
  const width = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  assert(width.document <= width.viewport + 1);
  checked(name, width);
}
async function selectTask(task) {
  await page.locator('.task-search input').fill(task.title);
  const cards = page.locator('.task-card');
  await cards.first().waitFor();
  let selected = false;
  for (let index = 0; index < await cards.count(); index++) {
    await cards.nth(index).click();
    await page.locator('.detail-panel .markdown').waitFor({ timeout: 20000 });
    const href = await page.getByRole('link', { name: '下载', exact: true }).getAttribute('href');
    if (href === `/api/research/tasks/${task.id}/report/download`) { selected = true; break; }
  }
  assert(selected, 'Could not select the requested real task through the task list');
}

(async () => {
  try {
    browser = await playwright.chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined, downloadsPath: path.join(temporary, 'downloads'), args: ['--disable-background-networking', '--no-first-run'] });
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', reducedMotion: 'reduce', acceptDownloads: true });
    await context.route('**/*', async route => {
      const target = new URL(route.request().url());
      if (['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname) || ['data:', 'blob:'].includes(target.protocol)) await route.continue();
      else { report.blocked_remote_requests.push(target.origin); await route.abort(); }
    });
    page = await context.newPage();
    page.on('pageerror', error => report.page_errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') report.console_errors.push(message.text()); });
    const config = await (await context.request.get(base + '/api/config')).json();
    assert.equal(config.live_ready, true, 'Complete online configuration is not ready');
    assert.equal(config.redis.vector_search_available, true);
    checked('live_services_and_redis_vector_capability_ready');
    const tasks = await (await context.request.get(base + '/api/research/tasks')).json();
    const chosen = process.env.UI_LIVE_TASK_ID ? tasks.find(task => task.id === process.env.UI_LIVE_TASK_ID) : tasks.find(task => task.mode === 'live' && task.status === 'succeeded');
    assert(chosen, 'A completed real online task is required; this review never creates tasks');
    const task = await (await context.request.get(`${base}/api/research/tasks/${chosen.id}`)).json();
    assert.equal(task.mode, 'live');
    assert.equal(task.status, 'succeeded');
    assert(task.report_markdown && task.metrics.total_calls > 0);
    report.task_id = task.id;
    const sources = await (await context.request.get(`${base}/api/research/tasks/${task.id}/sources`)).json();
    assert(sources.length > 0 && sources.every(source => source.task_id === task.id));
    assert(!sources.some(source => source.url?.startsWith('sample://')));
    const publicSources = sources.filter(source => /^https?:\/\//.test(source.url || ''));
    assert(publicSources.length > 0);
    checked('real_online_task_with_current_task_sources', { task_id: task.id, sources: sources.length, public_sources: publicSources.length, tool_calls: task.metrics.total_calls });
    const events = await (await context.request.get(`${base}/api/research/tasks/${task.id}/events`)).json();
    assert(events.some(event => event.event_type === 'model_completed'));
    assert(events.some(event => event.event_type === 'task_completed'));
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '研究工作台', exact: true }).waitFor();
    await selectTask(task);
    await page.locator('.detail-heading .mode-tag.live').waitFor();
    assert((await page.locator('.markdown').innerText()).length > 200);
    checked('online_report_displayed_with_online_mode');
    const modeNotice = await page.locator('.mode-notice').innerText();
    assert.match(modeNotice, /当前为在线研究/);
    assert.match(modeNotice, /真实模型/);
    assert.match(modeNotice, /模拟内容仍会单独标注/);
    assert.doesNotMatch(modeNotice, /不调用真实大模型/);
    checked('online_notice_matches_selected_task_and_preserves_simulated_data_boundary');
    const planText = await page.locator('.plan-list').innerText();
    assert.match(planText, /检索公开资料与核查来源|检索产品资料与核对口径|计算经营指标并生成图表/);
    assert.doesNotMatch(planText, /(?:dataset|doc|source)_[A-Za-z0-9]+/);
    checked('research_plan_uses_business_actions_without_internal_resource_ids');
    await screenshot('report-desktop');
    await noOverflow('online_desktop_report_no_overflow');
    const chart = page.locator('.detail-panel img').first();
    // Poll the currently rendered node: overview refreshes may replace Markdown
    // images, so retaining an ElementHandle makes this read-only check flaky.
    await page.waitForFunction(() => {
      const image = document.querySelector('.detail-panel img');
      return image && image.complete && image.naturalWidth > 100;
    });
    await chart.evaluate(image => image.scrollIntoView({ block: 'center' }));
    checked('online_real_chart_loaded', await chart.evaluate(image => ({ width: image.naturalWidth, height: image.naturalHeight })));
    const chartBox = await chart.boundingBox();
    assert(chartBox && chartBox.width > 100 && chartBox.height > 100);
    await page.screenshot({ path: path.join(output, 'report-chart.png'), clip: chartBox });
    report.screenshots.push('artifacts/ui-live/report-chart.png');
    const downloaded = page.waitForEvent('download');
    await page.getByRole('link', { name: '下载', exact: true }).click();
    const download = await downloaded;
    const downloadedPath = path.join(output, 'downloaded-online-report.md');
    await download.saveAs(downloadedPath);
    assert.equal(fs.readFileSync(downloadedPath, 'utf8'), task.report_markdown);
    checked('online_download_matches_saved_report');
    await page.getByRole('tab', { name: /证据来源/ }).click();
    await page.waitForFunction(count => document.querySelectorAll('.source-card').length === count, sources.length);
    checked('online_sources_visible', { cards: await page.locator('.source-card').count() });
    await screenshot('sources-desktop');
    await page.getByRole('tab', { name: /执行过程/ }).click();
    await page.waitForFunction(count => document.querySelectorAll('.timeline li').length === count, events.length);
    checked('online_execution_timeline_visible', { events: events.length });
    await screenshot('timeline-desktop');
    await page.reload({ waitUntil: 'networkidle' });
    await selectTask(task);
    await page.getByRole('tab', { name: /执行过程/ }).click();
    await page.waitForFunction(count => document.querySelectorAll('.timeline li').length === count, events.length);
    checked('online_refresh_recovers_report_and_unique_events');
    await nav('服务配置');
    await page.getByText('在线配置已就绪', { exact: true }).waitFor();
    checked('online_ready_displayed_in_configuration');
    await screenshot('settings-desktop');
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow('online_mobile_settings_no_overflow');
    await screenshot('settings-mobile');
    await nav(/研究工作台/);
    await page.getByRole('tab', { name: '研究报告', exact: true }).click();
    await noOverflow('online_mobile_report_no_overflow');
    await page.locator('.detail-heading').scrollIntoViewIfNeeded();
    await screenshot('report-mobile');
    assert.equal(report.page_errors.length, 0);
    assert.equal(report.blocked_remote_requests.length, 0);
    const actionable = report.console_errors.filter(message => !/WebSocket.*closed before the connection is established/.test(message));
    assert.equal(actionable.length, 0);
    checked('online_review_has_no_page_errors_or_external_browser_requests');
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.error = error.stack || String(error);
    if (page && !page.isClosed()) { try { await screenshot('failure'); } catch (_) {} }
    process.stderr.write(report.error + '\n');
    process.exitCode = 1;
  } finally {
    if (context) await context.close();
    if (browser) await browser.close();
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
    process.stdout.write(`Result: ${path.join(output, 'result.json')}\n`);
  }
})();
