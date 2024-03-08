import pandas as pd
from dotenv import load_dotenv
import os
load_dotenv()
import nltk
os.environ['NLTK_DATA'] = os.getenv('CACHE_DIR')
nltk.download("punkt")
from nltk.tokenize import sent_tokenize

import re
import random
import torch
from tqdm import tqdm
from torch.utils.data import Dataset, DataLoader, random_split
import json

DATA_DIR ="../data/"

def flatten_list_of_lists(list_of_lists):
    return [item for sublist in list_of_lists for item in sublist]

class DialogData:
    def __init__(self, file):
        self.file = file
        self.dialogues_raw = self.read_file()
 
    def read_file(self):
        raise NotImplementedError("Subclass must implement abstract method")

    def get_dialogues(self):
        raise NotImplementedError("Subclass must implement abstract method")

    def get_all_sentences(self):
        dialogues = self.get_dialogues()
        utterances = [utterance for dialogue in dialogues for utterance in dialogue]
        sentences = [sent_tokenize(utterance) for utterance in utterances]
        # filter '.' sentences
        filtered_sentences = [sentence for sentence in flatten_list_of_lists(sentences) if sentence.strip() != "."]
        return filtered_sentences

class DialogSum(DialogData):
    def __init__(self, file=f"{DATA_DIR}DialogSum/dialogsum.train.jsonl"):
        super().__init__(file)

    def read_file(self):
        return pd.read_json(self.file, lines=True)

    def get_dialogues(self):
        return list(self.dialogues_raw.dialogue.apply(lambda x: [utterance.split(': ', 1)[1] for utterance in x.split("\n")]))

class DailyDialog(DialogData):
    def __init__(self, file=f"{DATA_DIR}dialogues_text.txt"):
        super().__init__(file)

    def read_file(self):
        with open(self.file, 'r') as file:
            content = file.read()
        return content.strip().split('\n')
    
    def get_dialogues(self):
        dialogues = [dialogue.strip().split(' __eou__') for dialogue in self.dialogues_raw]
        processed_dialogues = []
        for dialogue in dialogues:
            processed_utterances = [self.process_utterance(utterance) for utterance in dialogue if utterance]
            processed_dialogues.append(processed_utterances)
        return processed_dialogues

    def process_utterance(self, utterance):
        # Remove unwanted spaces before punctuation
        utterance = re.sub(r'\s+([?!.,])', r'\1', utterance)
        # Replace spaces surrounding an apostrophe
        utterance = re.sub(r'\s+’\s+', "'", utterance)
        return utterance.strip()


class WoW(DialogData):
    def __init__(self, file=f'{DATA_DIR}wow/train.json', n=None):
        super().__init__(file)
        self.n = n

    def read_file(self):
        with open(self.file, 'r') as file:
            return json.load(file)

    def get_dialogues(self):
        dialogues = []
        for dialogue in self.dialogues_raw[:self.n] if self.n is not None else self.dialogues_raw:
            dialogue_texts = [turn['text'] for turn in dialogue['dialog']]
            dialogues.append(dialogue_texts)
        return dialogues

class CEFRTexts():
    def __init__(self, file=f"{DATA_DIR}cefr_leveled_texts.csv"):
        self.texts = pd.read_csv(file)

    def get_beginnings(self, min_length):
        return self.texts.text.apply(lambda text: sent_tokenize(text)[0].replace("\ufeff", ""))

    def get_all_sentences(self):
        self.texts["sentences"] = self.texts.text.apply(sent_tokenize)
        self.texts = self.texts.dropna().explode("sentences")
        return list(self.texts.sentences)

def get_mixed_sentences(n_per_corpus=1000):
    sentences = []
    corpora = [DailyDialog, DialogSum, WoW, CEFRTexts]
    for i, corpus in tqdm(enumerate(corpora), total=len(corpora)):
        corpus_inst = corpus()
        corpus_sents = corpus_inst.get_all_sentences()
        random.shuffle(corpus_sents)
        sentences += set(corpus_sents)
        sentences = sentences[:(i+1)*n_per_corpus]
    return sentences

class SentenceDataset(Dataset):
    def __init__(self, sentences, labels, tokenizer, max_len):
        self.sentences = sentences
        self.labels = labels
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.sentences)

    def __getitem__(self, idx):
        encoding = self.tokenizer(self.sentences[idx], return_tensors='pt', truncation=True, max_length=self.max_len, padding='max_length')
        return {
            'input_ids': encoding['input_ids'].flatten(),
            'attention_mask': encoding['attention_mask'].flatten(),
            'labels': torch.tensor(self.labels[idx], dtype=torch.long)
        }

def get_egp():
    egp = pd.read_excel(f'{DATA_DIR}English Grammar Profile Online.xlsx')
    # remove learner information from examples
    egp['Example'] = egp['Example'].str.replace(r"\(.*\)", "", regex=True).str.strip()
    egp['Type'] = egp['guideword'].apply(lambda x: 'FORM/USE' if 'FORM/USE' in x 
                                         else 'USE' if 'USE' in x 
                                         else 'FORM' if 'FORM' in x 
                                         else x)
    return egp

def get_dataset(positives, negatives, others, tokenizer, max_len, others_ratio = 3, verbose=False):
    unique_positive = list(set(positives)) # remove duplicates
    unique_negative = list(set(negatives).difference(set(positives))) # remove duplicates and positives
    num_rands = int(others_ratio * len(unique_negative))
    random.shuffle(others)
    sentences = unique_positive + unique_negative + others[:num_rands]
    labels = [1] * len(unique_positive) + [0] * len(unique_negative) + [0] * len(others[:num_rands])
    if verbose: print(sum(labels) / len(labels))
    return SentenceDataset(sentences, labels, tokenizer, max_len)

def get_loaders(dataset, batch_size=32):
    total_size = len(dataset)
    train_size = int(0.8 * total_size)
    val_size = total_size - train_size
    train_dataset, val_dataset = random_split(dataset, [train_size, val_size])
    train_dataloader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_dataloader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)
    return train_dataloader, val_dataloader