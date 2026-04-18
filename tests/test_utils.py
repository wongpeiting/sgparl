from sgparl.utils import get_mp_name, count_syllables, calc_number_of_sentences


class TestGetMpName:
    def test_standard_mp_name(self):
        assert get_mp_name("Mr Leong Mun Wai") == "Leong Mun Wai"

    def test_dr_prefix(self):
        assert get_mp_name("Dr Tan See Leng") == "Tan See Leng"

    def test_mdm_prefix(self):
        assert get_mp_name("Mdm Ho Geok Choo") == "Ho Geok Choo"

    def test_speaker_format(self):
        assert get_mp_name("SPEAKER (Mr Seah Kian Peng (Speaker)") == "Seah Kian Peng"

    def test_none_input(self):
        assert get_mp_name(None) == ""

    def test_empty_string(self):
        assert get_mp_name("") == ""

    def test_no_prefix_match(self):
        assert get_mp_name("Some Random Text") == ""


class TestCountSyllables:
    def test_one_syllable(self):
        assert count_syllables("cat") == 1

    def test_two_syllables(self):
        assert count_syllables("happy") == 2

    def test_silent_e(self):
        assert count_syllables("make") == 1

    def test_empty_word(self):
        assert count_syllables("") == 1


class TestCalcNumberOfSentences:
    def test_single_sentence(self):
        assert calc_number_of_sentences("Hello world.") == 1

    def test_multiple_sentences(self):
        assert calc_number_of_sentences("Hello. World! How?") == 3

    def test_no_punctuation(self):
        assert calc_number_of_sentences("Hello world") == 1
