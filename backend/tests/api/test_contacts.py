"""`GET /api/contacts` - every company/founder pair with view filters
(state, hiring, batch, email_status). D19: the hiring filter is a view
control, never a gate on who is contactable."""


def test_contacts_lists_every_company_and_founder(client, two_founders):
    assert len(client.get("/api/contacts").json()) == 2


def test_contacts_filter_by_state(client, two_founders):
    rows = client.get("/api/contacts?state=new").json()
    assert all(r["state"] == "new" for r in rows)


def test_hiring_filter_is_a_view_control_not_a_gate(client, founder_with_senior_posting):
    # D19: non-hiring companies are still listed by default.
    assert client.get("/api/contacts").json()
