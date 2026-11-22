"""The console's fixes for the demo (workstream extui): the new-metric buttons and their answers (X3), and the
readability of the incident feed and report (X4). The page's JavaScript runs in Node (tests/test_ui_runtime.py)."""
import re
from pathlib import Path

import yaml

from tests.test_ui_runtime import run_ui

PAGE = Path(__file__).resolve().parents[1] / "ui/static/index.html"


def html() -> str:
    return PAGE.read_text(encoding="utf-8")


def page_var(name: str) -> str:
    return re.search(rf"var {name} = '([^']+)';", html()).group(1).replace("\\n", "\n")


# ---------------------------------------------------------------------------- X3: the new-metric buttons
def test_the_refund_rate_button_posts_the_query_only():
    spec = yaml.safe_load(page_var("y"))
    assert spec["name"] == "refund_rate" and "formula" not in spec and "SUM(refunds)" in spec["sql"]
    assert 'id="nm-go">Add refund_rate<' in html()


def test_the_duplicate_button_posts_an_existing_rate_under_another_name():
    spec = yaml.safe_load(page_var("dup"))
    assert spec["name"] == "payment_rate" and "formula" not in spec
    assert "SUM(payment_successes)" in spec["sql"] and "SUM(payment_attempts)" in spec["sql"]
    assert re.search(r'id="nm-dup"[^>]*>Add a duplicate metric<', html())
    assert html().index('id="nm-dup"') < html().index("<main>")          # in the folded demo bar


CHECKS = r"""
const answer = {name:'payment_rate', status:'rejected', alias_of:'payment_success_rate',
  message:'payment_rate rejected: it is an alias of payment_success_rate: payment_successes / payment_attempts gives the same value as payment_success_rate for any data, so no new node was added',
  checks:[{check:'read',status:'pass',detail:'definition read: name and sql'},
          {check:'formula',status:'pass',detail:'found payment_successes / payment_attempts: the only one of 1045 candidates'},
          {check:'alias',status:'fail',detail:'it is an alias of payment_success_rate'}]};
"""


def test_the_toast_shows_the_answer_and_a_link_to_its_checks():
    run_ui(CHECKS + r"""
graph = {nodes:[], edges:[], trees:[]}; meta = {}; applyStyles = () => {};
answerToast(answer);
const toastText = $('toast').textContent;
assert.ok(toastText.startsWith(answer.message), toastText);
const link = $('toast').children.filter(c => c.textContent === 'Show checks');
assert.equal(link.length, 1);
link[0].onclick();
assert.equal(mode, 'node'); assert.equal(selNode, 'payment_rate');
const page = $('detail').textContent;
assert.ok(page.includes('Checks of the extension agent'), page);
assert.ok(page.includes('found payment_successes / payment_attempts: the only one of 1045 candidates'), page);
assert.ok(page.includes('alias') && page.includes('fail'), page);
assert.ok(page.includes(answer.message), page);
""")


def test_clicking_a_contributed_metric_lists_the_checks_from_its_result_file():
    run_ui(r"""
const result = {name:'refund_rate', status:'validated', message:'refund_rate added to the graph and validated',
  checks:[{check:'formula',status:'pass',detail:'found refunds / orders: the only one of 1045 candidates'},
          {check:'context',status:'pass',detail:'refunds and orders carry no release system'}]};
const asked = [];
get = url => { asked.push(url); return url === '/api/metric/refund_rate' ? Promise.resolve(result) : new Promise(() => {}); };
graph = {nodes:[{id:'refund_rate',kind:'metric',origin:'contributed',status:'validated'},
                {id:'orders',kind:'metric',origin:'core',status:'validated'}], edges:[], trees:[]};
meta = {refund_rate:graph.nodes[0], orders:graph.nodes[1]}; applyStyles = () => {};
(async () => {
  selNode = 'refund_rate'; mode = 'node'; renderDetail();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(asked, ['/api/metric/refund_rate']);
  const page = $('detail').textContent;
  assert.ok(page.includes('Checks of the extension agent'), page);
  assert.ok(page.includes('found refunds / orders: the only one of 1045 candidates'), page);
  selNode = 'orders'; renderDetail();                                    // a core metric has no answer to show
  assert.ok(!$('detail').textContent.includes('Checks of the extension agent'));
  assert.deepEqual(asked, ['/api/metric/refund_rate']);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
