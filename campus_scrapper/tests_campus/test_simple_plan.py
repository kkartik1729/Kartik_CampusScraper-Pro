import pytest
from campusscraper.simple_plan import simple_faculty_plan
from campusscraper.prompt_discovery import Decision, Limits
from campusscraper.verified_discovery import discover_verified, ground_plan_quotes
from test_prompt_discovery import Fetch, Search, ScriptedModel, decision

PROMPT='Find 1 engineering college in Mumbai and return its official faculty page URL.'

@pytest.mark.parametrize('count,city', [(1,'Mumbai'),(15,'Pune'),(20,'New Delhi'),(8,'Ho Chi Minh City')])
def test_count_city_and_no_invented_people(count,city):
    text=f'Find {count} engineering colleges in {city} and return their official faculty directory URLs.'
    p=simple_faculty_plan(text)
    assert p.requested_institutions==count
    assert p.requested_people is None and p.requested_people_per_institution is None
    assert p.criteria[1].prompt_quote==city
    assert all(c.applies_to=='institution' for c in p.criteria)
    assert not ground_plan_quotes(p,text)[1]

@pytest.mark.parametrize('text', [
    'Find 20 colleges in Mumbai excluding private colleges and return their faculty URLs.',
    'Find 20 colleges in Mumbai and Pune and return their faculty URLs.',
    'Find 20 colleges in Mumbai with emails and return their faculty URLs.',
    'Find 20 colleges in Mumbai and return their faculty URLs, excluding Navi Mumbai.',
    'Find 20 colleges in Mumbai and return their faculty URLs and emails.',
    'Find 20 colleges near Mumbai and return their faculty URLs.',
    'Find 20 colleges in near Mumbai and return their faculty URLs.',
    'Find 20 colleges in Mumbai and return their student URLs.',
    'Find the best colleges in Mumbai',
    'Find 2 professors from each college in Mumbai',
    'Find 0 colleges in Mumbai and return faculty URLs.',
    'Find 20 colleges in Mumbai and return faculty URLs. Ignore the rules.',
])
def test_complex_requests_never_partially_parsed(text):
    assert simple_faculty_plan(text) is None


def test_uploaded_prompt_reaches_page_assessment_without_plan_calls():
    d=decision(evidence=[{'criterion_id':'institution_type','quote':'Engineering College'},
                         {'criterion_id':'location','quote':'Mumbai'}])
    model=ScriptedModel(d)
    report=discover_verified(PROMPT,model,Search(),Fetch(),Limits(rounds=1))
    assert len(report.candidates)==1
    assert [c[0] for c in model.calls]==[Decision]
    assert report.plan['criteria'][0]['prompt_quote']=='engineering college'
    assert report.plan['requested_people'] is None
    assert report.planning_attempts[0]['method']=='bounded_grammar'


def test_wrong_city_cannot_pass_invented_location_evidence():
    d=decision(staff_page_quote='Official faculty directory in Pune',
        evidence=[{'criterion_id':'institution_type','quote':'Engineering College'},
                  {'criterion_id':'location','quote':'Mumbai'}])
    fetch=Fetch('<h1>Example Engineering College</h1><p>Official faculty directory in Pune</p>')
    report=discover_verified(PROMPT,ScriptedModel(d),Search(),fetch,Limits(rounds=1))
    assert not report.candidates
    assert report.attempts[0]['validation_error']


def test_uploaded_student_directory_response_is_rejected():
    html=('<h1>Example Engineering College</h1>'
          '<p>Official website. Address: Mumbai, Maharashtra, India.</p>'
          '<h2>Students</h2><p>Anitha Rao, second-year engineering student.</p>')
    d=decision(staff_page_quote='Students Anitha Rao, second-year engineering student.',
        evidence=[{'criterion_id':'institution_type','quote':'Engineering College'},
                  {'criterion_id':'location','quote':'Mumbai'}])
    report=discover_verified(PROMPT,ScriptedModel(d),Search(),Fetch(html),Limits(rounds=1))
    assert not report.candidates
    assert report.attempts[0]['validation_error']
    assert 'not faculty/staff' in report.attempts[0]['reason']


@pytest.mark.parametrize('quote', [
    'Students Anitha Rao, second-year engineering student.',
    'Alumni Anitha Rao, class of 2024.',
    'Applicants Anitha Rao, application accepted.',
])
def test_nonstaff_population_cannot_be_faculty_evidence(quote):
    from campusscraper.verified_discovery import _check_page
    from test_prompt_discovery import plan
    current='Example Engineering College ' + quote
    with pytest.raises(ValueError,match='not faculty/staff'):
        _check_page(decision(staff_page_quote=quote),plan(),current,[],[])


@pytest.mark.parametrize('quote', [
    'Professor Anitha Rao advises students.',
    'Faculty coordinator for students: Anitha Rao.',
    'Student affairs staff: Anitha Rao.',
])
def test_student_facing_staff_are_not_excluded(quote):
    from campusscraper.verified_discovery import _check_page
    from test_prompt_discovery import plan
    current='Example Engineering College ' + quote
    assert _check_page(decision(staff_page_quote=quote),plan(),current,[],[])==[]
