/* Isolated UI smoke test. Never connects to an existing browser or user profile.
 * Set PLAYWRIGHT_LIBRARY_PATH to an installed Playwright directory when it is
 * not available as a normal Node module. No provider credentials are required.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const output = path.resolve(process.env.UI_ARTIFACT_DIR || path.join(root, 'artifacts', 'ui'));
const cacheRoot = path.resolve(root, '.cache');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(cacheRoot, { recursive: true });
const temporary = fs.mkdtempSync(path.join(cacheRoot, 'ui-smoke-'));
const playwright = require(process.env.PLAYWRIGHT_LIBRARY_PATH || 'playwright');
const base = localURL(process.env.UI_BASE_URL || `http://127.0.0.1:${process.env.UI_TEST_PORT || '8123'}`);
const deliveryURL = process.env.UI_DELIVERY_URL === '' ? null : localURL(process.env.UI_DELIVERY_URL || 'http://127.0.0.1:8000');
const report = { started_at: new Date().toISOString(), base, isolated_browser: true, isolated_data: !process.env.UI_BASE_URL, checks: [], console_errors: [], page_errors: [], failed_requests: [], blocked_remote_requests: [], screenshots: [] };
const websocketFrames = [];
let server;
let browser;
let context;
let page;
let serverLog = '';

function localURL(value) {
  const parsed = new URL(value);
  assert(['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname), 'UI test only permits loopback project URLs');
  assert.equal(parsed.protocol, 'http:', 'Local UI test expects HTTP');
  assert(!parsed.username && !parsed.password, 'Credentials are not accepted in test URLs');
  return parsed.origin;
}

function checked(name, details = {}) {
  report.checks.push({ name, passed: true, ...details });
  process.stdout.write(`PASS ${name}\n`);
}

async function waitForServer(url) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (server && server.exitCode !== null) throw new Error(`Test server exited: ${serverLog.slice(-2000)}`);
    try {
      const response = await fetch(url + '/api/health', { signal: AbortSignal.timeout(1500) });
      if (response.ok) return;
    } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('Local test server did not become ready');
}

async function screenshot(name, target = page) {
  const targetPath = path.join(output, name + '.png');
  await target.screenshot({ path: targetPath, fullPage: false });
  report.screenshots.push(path.relative(root, targetPath).replaceAll('\\', '/'));
}

async function assertNoOverflow(name) {
  const geometry = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  assert(geometry.document <= geometry.viewport + 1, `${name}: document width ${geometry.document} exceeds ${geometry.viewport}`);
  assert(geometry.body <= geometry.viewport + 1, `${name}: body width ${geometry.body} exceeds ${geometry.viewport}`);
  checked(name, geometry);
}

async function nav(name) {
  const mobile = await page.getByRole('button', { name: '打开导航', exact: true }).isVisible();
  if (mobile) await page.getByRole('button', { name: '打开导航', exact: true }).click();
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name }).click();
}

async function createdTask(action) {
  const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/research/tasks' && response.request().method() === 'POST' && response.status() === 202);
  await action();
  return (await responsePromise).json();
}

async function waitForReport(task) {
  await page.locator('.detail-panel .markdown').waitFor({ state: 'visible', timeout: 60000 });
  await page.locator('.detail-panel .badge.succeeded').waitFor({ state: 'visible', timeout: 60000 });
  const response = await context.request.get(base + '/api/research/tasks/' + task.id);
  assert(response.ok());
  const result = await response.json();
  assert.equal(result.status, 'succeeded', result.error || 'Research did not succeed');
  return result;
}

async function run() {
  if (!process.env.UI_BASE_URL) {
    const python = process.env.PYTHON_EXECUTABLE || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    assert(fs.existsSync(python), 'Install backend dependencies before UI smoke test');
    const parsed = new URL(base);
    server = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', parsed.port], {
      cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUTF8: '1', RESEARCH_DATA_DIR: path.join(temporary, 'data'), RESEARCH_REDIS_URL: '', RESEARCH_MODE: 'demo', RESEARCH_DEMO_STEP_DELAY: '0.15', RESEARCH_MODEL_NAME: '', RESEARCH_MODEL_BASE_URL: '', RESEARCH_MODEL_API_KEY: '', RESEARCH_SEARCH_API_KEY: '', RESEARCH_SEARCH_BASE_URL: '', RESEARCH_EMBEDDING_MODEL: '', RESEARCH_EMBEDDING_BASE_URL: '', RESEARCH_EMBEDDING_API_KEY: '' },
    });
    server.stdout.on('data', data => { serverLog += data.toString(); });
    server.stderr.on('data', data => { serverLog += data.toString(); });
  }
  await waitForServer(base);
  browser = await playwright.chromium.launch({ headless: true, downloadsPath: path.join(temporary, 'downloads'), executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined, args: ['--disable-background-networking', '--no-first-run'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', reducedMotion: 'reduce', acceptDownloads: true });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)) await route.continue();
    else {
      report.blocked_remote_requests.push(url.origin);
      await route.abort();
    }
  });
  page = await context.newPage();
  page.on('console', message => { if (message.type() === 'error') report.console_errors.push(message.text()); });
  page.on('pageerror', error => report.page_errors.push(error.message));
  page.on('requestfailed', request => report.failed_requests.push({ url: request.url(), error: request.failure()?.errorText }));
  page.on('websocket', socket => socket.on('framereceived', frame => {
    try {
      const event = JSON.parse(String(frame.payload));
      if (typeof event.seq === 'number') websocketFrames.push(event);
    } catch (_) {}
  }));

  if (deliveryURL && deliveryURL !== base) {
    const response = await page.goto(deliveryURL, { waitUntil: 'networkidle' });
    assert(response && response.ok(), 'Delivery page did not load');
    await page.getByRole('heading', { name: '研究工作台', exact: true }).waitFor();
    await screenshot('delivery-desktop');
    checked('delivery_page_load', { url: deliveryURL });
  }

  await page.goto(base, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: '你的第一份研究，从这里开始', exact: true }).waitFor();
  const initialTasks = await (await context.request.get(base + '/api/research/tasks')).json();
  assert.equal(initialTasks.length, 0, 'An empty isolated data directory is required for the empty-state check');
  checked('empty_state_has_no_fake_tasks');
  await assertNoOverflow('desktop_empty_no_horizontal_overflow');
  await screenshot('empty-desktop');

  await nav('资料与数据');
  await page.getByRole('button', { name: '加载示例资料', exact: true }).click();
  await page.getByText('orders.csv', { exact: true }).waitFor();
  const samples = await (await context.request.get(base + '/api/datasets')).json();
  assert.equal(samples.length, 1);
  assert.equal(samples[0].row_count, 19);
  checked('sample_resources_loaded_through_ui', { dataset_rows: samples[0].row_count });
  await nav(/研究工作台/);

  const first = await createdTask(() => page.getByRole('button', { name: '体验示例研究', exact: true }).click());
  const complete = await waitForReport(first);
  const text = await page.locator('.markdown').innerText();
  assert.match(text, /3,?900(?:\.00)?/);
  assert.match(text, /3,?720(?:\.00)?/);
  assert.match(text, /去重订单量\s*16/);
  assert.match(text, /未调用真实大模型或实时联网搜索/);
  assert(complete.metrics.total_calls >= 9);
  checked('sample_research_report_business_numbers', { task_id: first.id, total_calls: complete.metrics.total_calls, sales: 3900, net_sales: 3720, orders: 16 });
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('link', { name: '下载', exact: true }).click();
  const download = await downloadPromise;
  const reportPath = path.join(output, 'downloaded-report.md');
  await download.saveAs(reportPath);
  assert.equal(fs.readFileSync(reportPath, 'utf8'), complete.report_markdown);
  checked('download_matches_saved_markdown_report', { filename: download.suggestedFilename() });

  const chart = page.locator('.markdown img').first();
  await chart.scrollIntoViewIfNeeded();
  await page.waitForFunction(img => img.complete && img.naturalWidth > 0, await chart.elementHandle());
  const imageSize = await chart.evaluate(img => ({ width: img.naturalWidth, height: img.naturalHeight, src: img.getAttribute('src') }));
  assert(imageSize.width > 100 && imageSize.height > 100);
  await chart.screenshot({ path: path.join(output, 'report-chart.png') });
  report.screenshots.push(path.relative(root, path.join(output, 'report-chart.png')).replaceAll('\\', '/'));
  checked('real_chart_image_loaded', imageSize);
  await page.locator('.detail-heading').scrollIntoViewIfNeeded();
  await screenshot('report-desktop');
  await assertNoOverflow('desktop_report_no_horizontal_overflow');

  await page.getByRole('tab', { name: /执行过程/ }).click();
  await page.locator('.timeline li').first().waitFor();
  const eventCount = await page.locator('.timeline li').count();
  assert(eventCount >= 18);
  const events = await (await context.request.get(base + `/api/research/tasks/${first.id}/events`)).json();
  assert(events.some(event => event.event_type === 'tool_completed'));
  checked('actual_execution_timeline', { event_count: eventCount });
  await page.locator('.connection.live').waitFor();
  assert(websocketFrames.some(event => event.task_id === first.id && event.event_type === 'tool_completed'));
  const liveFrameCount = websocketFrames.filter(event => event.task_id === first.id).length;
  const cursor = events[Math.floor(events.length / 2)].seq;
  const expected = events.filter(event => event.seq > cursor).map(event => event.seq);
  const replayed = await page.evaluate(({ id, after, count }) => new Promise((resolve, reject) => {
    const socket = new WebSocket(`ws://${location.host}/api/research/tasks/${id}/stream?after_seq=${after}`);
    const received = [];
    const timer = setTimeout(() => { socket.close(); reject(new Error('WebSocket replay timed out')); }, 10000);
    socket.onerror = () => { clearTimeout(timer); reject(new Error('WebSocket replay failed')); };
    socket.onmessage = message => {
      const event = JSON.parse(message.data);
      if (typeof event.seq === 'number') received.push(event.seq);
      if (received.length >= count) { clearTimeout(timer); socket.close(); resolve(received); }
    };
  }), { id: first.id, after: cursor, count: expected.length });
  assert.deepEqual(replayed, expected);
  checked('websocket_live_frames_and_cursor_replay', { live_frames: liveFrameCount, after_seq: cursor, replayed: replayed.length });
  await screenshot('timeline-desktop');

  await page.getByRole('tab', { name: /证据来源/ }).click();
  await page.locator('.source-card').first().waitFor();
  const sourceCount = await page.locator('.source-card').count();
  assert(sourceCount >= 3);
  assert.match(await page.locator('.source-list').innerText(), /离线|虚构|模拟/);
  checked('task_sources_visible', { source_count: sourceCount });

  await nav('运行观察');
  await page.waitForFunction(() => document.querySelectorAll('.metrics-table-panel tbody tr').length === 9);
  const toolRows = await page.locator('.metrics-table-panel tbody tr').count();
  assert.equal(toolRows, 9);
  checked('actual_metrics_for_all_nine_tools', { tool_rows: toolRows });
  await screenshot('metrics-desktop');

  await page.getByRole('button', { name: '新建研究', exact: true }).last().click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('#research-question').fill('仅根据上传CSV计算咖啡用品销售额和订单量，不联网、不查询知识库');
  await dialog.getByLabel(/orders\.csv/).check();
  await dialog.getByLabel('开始日期', { exact: true }).fill('2026-01-01');
  await dialog.getByLabel('结束日期', { exact: true }).fill('2026-03-31');
  const second = await createdTask(() => dialog.getByRole('button', { name: '开始研究', exact: true }).click());
  const dataOnly = await waitForReport(second);
  assert.deepEqual([...new Set(dataOnly.plan.map(item => item.agent))], ['data']);
  const secondEvents = await (await context.request.get(base + `/api/research/tasks/${second.id}/events`)).json();
  assert(!secondEvents.some(event => ['web', 'knowledge'].includes(event.agent)));
  checked('data_only_task_selects_only_data_worker', { task_id: second.id, calls: dataOnly.metrics.total_calls });

  await page.reload({ waitUntil: 'networkidle' });
  const card = page.getByRole('button', { name: /仅根据上传CSV计算咖啡用品销售额和订单量/ });
  await card.waitFor();
  await card.click();
  await page.locator('.markdown').waitFor();
  checked('refresh_preserves_persisted_task_and_report');
  await page.getByRole('tab', { name: /执行过程/ }).click();
  await page.waitForFunction(count => document.querySelectorAll('.timeline li').length === count, secondEvents.length);
  checked('refresh_replays_timeline_without_duplicate_events', { events: secondEvents.length });

  await nav('资料与数据');
  const datasetUpload = page.waitForResponse(response => new URL(response.url()).pathname === '/api/datasets' && response.request().method() === 'POST' && response.status() === 201);
  await page.getByLabel('上传 CSV', { exact: true }).setInputFiles({ name: 'ui-upload-orders.csv', mimeType: 'text/csv', buffer: fs.readFileSync(path.join(root, 'samples', 'orders.csv')) });
  const uploadedDataset = await (await datasetUpload).json();
  assert.equal(uploadedDataset.row_count, 19);
  await page.getByText('ui-upload-orders.csv', { exact: true }).waitFor();
  const documentUpload = page.waitForResponse(response => new URL(response.url()).pathname === '/api/documents' && response.request().method() === 'POST' && response.status() === 201);
  await page.getByLabel('上传资料', { exact: true }).setInputFiles({ name: 'ui-upload-note.md', mimeType: 'text/markdown', buffer: Buffer.from('# 验证资料\n本资料仅用于本地界面上传验证。') });
  const uploadedDocument = await (await documentUpload).json();
  assert(uploadedDocument.chunk_count >= 1);
  await page.getByText('ui-upload-note.md', { exact: true }).waitFor();
  checked('csv_and_document_uploads_are_parsed_and_listed', { rows: uploadedDataset.row_count, chunks: uploadedDocument.chunk_count });

  await nav('服务配置');
  await page.getByText('在线研究还需配置', { exact: true }).waitFor();
  assert.match(await page.locator('.missing-config').innerText(), /model_api_key/);
  checked('missing_online_configuration_is_visible');
  await screenshot('settings-desktop');

  await page.setViewportSize({ width: 390, height: 844 });
  await assertNoOverflow('mobile_settings_no_horizontal_overflow');
  await screenshot('settings-mobile');
  await nav(/研究工作台/);
  await page.getByRole('tab', { name: '研究报告', exact: true }).click();
  await assertNoOverflow('mobile_report_no_horizontal_overflow');
  await page.locator('.detail-heading').scrollIntoViewIfNeeded();
  await screenshot('report-mobile');
  await page.getByRole('button', { name: '新建研究', exact: true }).last().click();
  await page.getByRole('dialog').waitFor();
  await assertNoOverflow('mobile_create_no_horizontal_overflow');
  await screenshot('create-mobile');
  await page.getByRole('button', { name: '关闭新建研究', exact: true }).click();

  assert.equal(report.page_errors.length, 0, 'Browser page errors occurred');
  assert.equal(report.blocked_remote_requests.length, 0, 'UI attempted a non-local network request');
  const cleanupMessages = /WebSocket.*closed before the connection is established/;
  report.cleanup_console_messages = report.console_errors.filter(message => cleanupMessages.test(message));
  report.actionable_console_errors = report.console_errors.filter(message => !cleanupMessages.test(message));
  assert.equal(report.actionable_console_errors.length, 0, 'Browser console errors occurred');
  report.download_handoffs = report.failed_requests.filter(request => request.url.endsWith('/report/download') && request.error === 'net::ERR_ABORTED');
  report.actionable_failed_requests = report.failed_requests.filter(request => !report.download_handoffs.includes(request));
  assert.equal(report.actionable_failed_requests.length, 0, 'Browser resource requests failed');
  checked('no_page_errors_or_nonlocal_requests', { cleanup_console_messages: report.cleanup_console_messages.length });
}

(async () => {
  try {
    await run();
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.error = error.stack || String(error);
    if (page && !page.isClosed()) {
      try { await screenshot('failure'); } catch (_) {}
    }
    process.stderr.write(report.error + '\n');
    process.exitCode = 1;
  } finally {
    if (context) await context.close();
    if (browser) await browser.close();
    if (server) {
      server.kill();
      await new Promise(resolve => { if (server.exitCode !== null) resolve(); else { server.once('exit', resolve); setTimeout(resolve, 3000); } });
      fs.writeFileSync(path.join(output, 'api.log'), serverLog, 'utf8');
    }
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
    process.stdout.write(`Result: ${path.join(output, 'result.json')}\n`);
  }
})();
