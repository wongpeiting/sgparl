# sgparl/parse.py
from datetime import datetime

from bs4 import BeautifulSoup
import pandas as pd

from sgparl.utils import (
    get_mp_name,
    count_syllables,
    calc_number_of_syllables,
    calc_number_of_sentences,
    count_words_and_characters,
)


# --- Sittings ---


def parse_sittings(metadata):
    """Parse sitting metadata into a single-row DataFrame."""
    date_string = metadata["sittingDate"]
    date_object = datetime.strptime(date_string, "%d-%m-%Y")
    date_str = date_object.strftime("%Y-%m-%d")

    datetime_string = f"{date_string} {metadata['startTimeStr']}"
    datetime_string = datetime_string.replace("noon", "PM")
    datetime_object = datetime.strptime(datetime_string, "%d-%m-%Y %I:%M %p")
    datetime_str = datetime_object.strftime("%Y-%m-%dT%H:%M:%S")

    return pd.DataFrame(
        {
            "date": [date_str],
            "datetime": [datetime_str],
            "parliament": [metadata["parlimentNO"]],
            "session": [metadata["sessionNO"]],
            "volume": [metadata["volumeNO"]],
            "sittings": [metadata["sittingNO"]],
        }
    )


# --- Attendance ---


def parse_attendance(date, attendance_list):
    """Parse attendance list into a DataFrame."""
    return pd.DataFrame(
        {
            "date": [date] * len(attendance_list),
            "member_name": [get_mp_name(obj["mpName"]) for obj in attendance_list],
            "is_present": [obj["attendance"] for obj in attendance_list],
        }
    )


# --- Topics ---


def _topic_cid(date, order):
    return f"{date}-T-{order:03}"


def parse_topics(date, topics_list):
    """Parse topics list into a DataFrame."""
    orders = list(range(1, len(topics_list) + 1))
    topic_cids = [_topic_cid(date, order) for order in orders]

    return pd.DataFrame(
        {
            "topic_id": topic_cids,
            "date": [date] * len(topics_list),
            "topic_order": orders,
            "title": [section["title"] for section in topics_list],
            "section_type": [section["sectionType"] for section in topics_list],
        }
    )


# --- Speeches ---


def _process_content(soup):
    """Extract speakers and texts from BeautifulSoup-parsed HTML content.

    Ported from transform/speeches.py:process_content.
    """
    speakers = []
    texts = []
    sequences = []

    for index, p in enumerate(soup.find_all("p")):
        try:
            if p.strong:
                if (
                    str(p.strong.text).strip() == ""
                    or len(str(p.strong.text).strip()) < 3
                ) and index > 0:
                    speaker = speakers[-1]
                else:
                    speaker = str(p.strong.text).strip()
                text = str(p.find("strong").next_sibling)
                if p.find("span"):
                    text = text + " " + p.find("span").get_text()
                sequence = 1
            else:
                if len(speakers) > 0:
                    speaker = speakers[-1] if index > 0 else ""
                    sequence = sequences[-1] + 1 if index > 0 else 1
                else:
                    if soup.find_all("p")[index - 1].strong.text.strip():
                        speaker = soup.find_all("p")[index - 1].strong.text.strip()
                    else:
                        speaker = ""
                    sequence = 1
                text = str(p.text)

            speakers.append(speaker)
            texts.append(
                text.strip()
                .replace("\xa0", " ")
                .replace("\t", " ")
                .replace(":", " ")
                .strip()
            )
            sequences.append(sequence)
        except Exception as e:
            print(f"  Warning: parse error at paragraph {index}: {e}")

    # Combine consecutive texts by same speaker
    revised_speakers = []
    revised_texts = []
    last_speaker = None

    for index in range(len(speakers)):
        if (
            last_speaker == speakers[index]
            and not texts[index].strip().lower().startswith("asked")
            and "to ask" not in texts[index].strip().lower()[:10]
        ):
            revised_texts[-1] += " " + texts[index]
        else:
            revised_speakers.append(speakers[index])
            revised_texts.append(texts[index])
            last_speaker = speakers[index]

    return revised_speakers, revised_texts


def _clean_rows(df):
    """Clean speech text: strip HTML, remove boilerplate.

    Ported from transform/speeches.py:clean_rows.
    """
    df["text"] = df["text"].apply(
        lambda x: BeautifulSoup(x, "html.parser").get_text()
    )
    df["text"] = df["text"].str.replace("proc text", "", case=False)
    df["text"] = df["text"].str.replace(r"Page  \d+", "", regex=True)
    df["text"] = df["text"].str.replace("None", "")
    df = df[df["text"].astype(str).str.strip() != ""]
    return df


def _speech_cid(row):
    return f"{row['topic_id']}-S-{row['speech_order']:05}"


def _parse_topic_speeches(content, topic_cid):
    """Parse speeches from a single topic's HTML content."""
    soup = BeautifulSoup(content, "html.parser")
    speakers, texts = _process_content(soup)

    cleaned_speakers = [get_mp_name(speaker) for speaker in speakers]

    df = pd.DataFrame(
        {
            "date": [topic_cid[:10]] * len(speakers),
            "topic_id": [topic_cid] * len(speakers),
            "member_name_original": speakers,
            "member_name": cleaned_speakers,
            "text": texts,
        }
    )

    if len(df) > 0:
        df = _clean_rows(df)

    return df


def parse_speeches(date, topics_list):
    """Parse all speeches across all topics into a DataFrame."""
    all_dfs = []
    orders = list(range(1, len(topics_list) + 1))
    topic_cids = [_topic_cid(date, order) for order in orders]

    for index, topic in enumerate(topics_list):
        content = topic.get("content")
        if not content:
            continue
        topic_cid = topic_cids[index]
        topic_df = _parse_topic_speeches(content, topic_cid)
        if len(topic_df) > 0:
            all_dfs.append(topic_df)

    if not all_dfs:
        return pd.DataFrame(
            columns=[
                "date", "speech_id", "topic_id", "speech_order",
                "member_name_original", "member_name", "text",
                "num_words", "num_characters", "num_sentences", "num_syllables",
            ]
        )

    df = pd.concat(all_dfs, ignore_index=True)

    # Add speech order and IDs
    df["speech_order"] = list(range(1, len(df) + 1))
    df["speech_id"] = df.apply(_speech_cid, axis=1)

    # Add text metrics
    df["num_sentences"] = df["text"].apply(calc_number_of_sentences)
    df["num_syllables"] = df["text"].apply(calc_number_of_syllables)
    wc = df["text"].apply(
        lambda t: pd.Series(
            count_words_and_characters(t), index=["num_words", "num_characters"]
        )
    )
    df = pd.concat([df, wc], axis=1)

    # Final column order
    return df[
        [
            "date", "speech_id", "topic_id", "speech_order",
            "member_name_original", "member_name", "text",
            "num_words", "num_characters", "num_sentences", "num_syllables",
        ]
    ]
