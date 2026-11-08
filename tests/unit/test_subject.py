"""Разбор субъекта запроса: роли, псевдонимы, режимы (docs/architecture/41)."""

from app.subject import Person, Project, parse_subject

INSECTS = Project(id=1, name="Жизнь насекомых")
PURGA = Project(id=2, name="Чёрная пурга")

GAGARINA = Person(id=10, name="Орлова Александра", aliases=[
    ("Саша", "short", None), ("Наташа", "role", 1), ("Наташа", "role", 2)])
TAYUKIN = Person(id=11, name="Новиков Виктор", aliases=[
    ("Витя", "short", None), ("Сэм", "role", 1)])
KONDR = Person(id=12, name="Морозова Алёна", aliases=[("Алёна", "short", None)])

PEOPLE = [GAGARINA, TAYUKIN, KONDR]
PROJECTS = [INSECTS, PURGA]


def test_роли_в_спектакле():
    subject = parse_subject("найди окна для Наташи и Сэма в Жизни насекомых", PEOPLE, PROJECTS)
    assert subject.mode == "subset"
    assert sorted(subject.participant_ids) == [10, 11]
    assert subject.project_ids == [1]
    assert subject.confidence >= 0.7


def test_роль_без_проекта():
    subject = parse_subject("окна для Сэма", PEOPLE, PROJECTS)
    assert subject.participant_ids == [11]


def test_короткое_имя():
    subject = parse_subject("когда свободна Саша", PEOPLE, PROJECTS)
    assert subject.participant_ids == [10]


def test_фамилия():
    subject = parse_subject("окна для Новикова", PEOPLE, PROJECTS)
    assert subject.participant_ids == [11]


def test_несколько_людей():
    subject = parse_subject("найди окна для Алёны и Саши", PEOPLE, PROJECTS)
    assert sorted(subject.participant_ids) == [10, 12]


def test_максимум_свободных():
    subject = parse_subject("в какой день провести тренинг, чтобы было больше всех", PEOPLE, PROJECTS)
    assert subject.mode == "max_free"


def test_тренинг_для_всех_это_максимум():
    subject = parse_subject("надо провести тренинг для труппы", PEOPLE, PROJECTS)
    assert subject.mode == "max_free"


def test_все_участники():
    subject = parse_subject("найди окна для всех", PEOPLE, PROJECTS)
    assert subject.mode == "all"


def test_пустой_запрос_это_все():
    assert parse_subject("", PEOPLE, PROJECTS).mode == "all"


def test_непонятное_имя_в_неразобранных():
    subject = parse_subject("окна для Наташи и Сэма и Марата", PEOPLE, PROJECTS)
    assert "марата" in subject.unresolved
    assert subject.confidence < 0.9


def test_роль_другого_проекта_не_путается():
    # «Наташа» есть в двух проектах, но «Сэм» только в «Жизни насекомых»
    subject = parse_subject("Сэм в Чёрной пурге", PEOPLE, PROJECTS)
    assert 11 not in subject.participant_ids or subject.confidence < 0.5


def test_проект_распознаётся_по_основе():
    subject = parse_subject("окна в жизни насекомых", PEOPLE, PROJECTS)
    assert subject.project_ids == [1]
