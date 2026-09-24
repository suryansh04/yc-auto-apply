"""The company's own site and its YC page on every letter (operator
request 2026-09-25: "give the company official website link to both their
product website and also yc website")."""


def _item(client, draft):
    return client.get(f"/api/queue/{draft.outreach.id}").json()


def test_the_letter_carries_both_links(client, repos, pending_draft):
    company = repos.company.rows[pending_draft.outreach.id]
    company.website, company.slug = "https://www.vahan.ai", "vahan"
    item = _item(client, pending_draft)
    assert item["company_website"] == "https://www.vahan.ai"
    assert item["company_yc_url"] == "https://www.ycombinator.com/companies/vahan"


def test_a_bare_domain_becomes_a_link_that_opens(client, repos, pending_draft):
    # YC stores some as "acme.com"; without a scheme the browser would
    # treat it as a path on this app.
    repos.company.rows[pending_draft.outreach.id].website = "vahan.ai"
    assert _item(client, pending_draft)["company_website"] == "https://vahan.ai"


def test_no_website_or_slug_means_no_link(client, repos, pending_draft):
    company = repos.company.rows[pending_draft.outreach.id]
    company.website, company.slug = None, ""
    item = _item(client, pending_draft)
    assert item["company_website"] is None and item["company_yc_url"] is None
