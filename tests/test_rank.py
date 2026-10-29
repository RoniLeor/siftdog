import pytest

from siftdog.rank import bm25_scores, chunk, combine_scores, score_groups, select_chunks, tokenize


def test_bm25_prefers_matching_doc():
    scores = bm25_scores("python install", ["the weather is nice", "install python today"])
    assert scores[1] > scores[0] == 0.0


def test_chunk_packs_paragraphs_and_wraps_long_ones():
    text = "short one\n\nshort two\n\n" + "word " * 300
    chunks = chunk(text, max_chars=100)
    assert chunks[0] == "short one\n\nshort two"
    assert all(len(c) <= 100 for c in chunks)


def test_select_chunks_keeps_document_order():
    assert select_chunks(["a", "b", "c"], [0.1, 0.9, 0.5], 2) == ["b", "c"]
    assert select_chunks(["a", "b"], [0.1, 0.9], 5) == ["a", "b"]


def test_score_groups_shares_idf_across_groups():
    # A page that repeats the term in every chunk must outscore one that mentions it once.
    # Scored page by page, the first page's IDF collapsed (the term was in all of its chunks).
    heavy = ["install install python"] * 3
    light = ["install python once", "some other words", "yet more words"]
    scores = score_groups("install", [heavy, light])
    assert [len(s) for s in scores] == [3, 3]
    assert max(scores[0]) > max(scores[1]) > 0


def test_combine_scores_bounded_and_rank_prior():
    scores = combine_scores([0.0, 0.0, 0.0])
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 1 for s in combine_scores([5.0, 1.0, 0.0]))


@pytest.mark.parametrize(
    "text, tokens",
    [
        ('ת"א', ["תא"]),
        ("ת״א", ["תא"]),
        ("ג'ינס", ["גינס"]),
        ("שָׁלוֹם", ["שלום"]),
        ("בית־ספר", ["בית", "ספר"]),  # maqaf still splits, like an ASCII hyphen
        ("don't", ["don", "t"]),
        ("café", ["café"]),
        ("המחשב של דני", ["המחשב", "דני"]),
    ],
)
def test_tokenize_hebrew_marks_abbreviations_and_stopwords(text, tokens):
    assert tokenize(text) == tokens


def test_bm25_matches_hebrew_abbreviation_forms():
    scores = bm25_scores('ת"א', ["תל אביב (ת״א)", "ירושלים"])
    assert scores[0] > 0 == scores[1]
