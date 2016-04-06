"""
This is like snli_processing
it preprocesses rt-polarity

convert it into a npz file similar
to SNLI
"""
import sys

"""
trim down GoogleNews-vector
based on the partial IMDB data corpus in CNN_Sentence
"""

import os, json
import numpy as np
import unicodedata
import re
from gensim.models.word2vec import Word2Vec
import time

label_idx_map = {'neg': 0, 'pos': 1}

word_idx_map = {'<NULL>': 0, '<UNK>': 1, '<END>': 2}

idx_word_map = ['<NULL>', '<UNK>', '<END>']

# we replace rare words with <UNK>, which shares the same vector
word_count_map = {}  # length: 18768

W_embed = None


def load_dataset(base_dir, dev_portion=0.05, test_portion=0.05):
    data = {}

    pos_sens_f = os.path.join(base_dir, 'rt-polarity.pos')

    neg_sens_f = os.path.join(base_dir, 'rt-polarity.neg')

    pos_sens = get_data('pos', pos_sens_f)  # 5331 about 20% of the IMDB database (25,000)
    neg_sens = get_data('pos', neg_sens_f)  # 5331
    # so about 40% in total of IMDB database

    # now we convert real word sentences into indices
    pos_sens['sentences'] = convert_words_to_idx(pos_sens['sentences'])
    neg_sens['sentences'] = convert_words_to_idx(neg_sens['sentences'])

    pos_choices = np.arange(len(pos_sens['sentences']))
    neg_choices = np.arange(len(neg_sens['sentences']))

    # then we shuffle them
    np.random.shuffle(pos_choices)
    np.random.shuffle(neg_choices)

    val_cut_off = int(np.ceil(len(pos_sens['sentences']) * dev_portion))
    test_cut_off = int(np.ceil(len(pos_sens['sentences']) * test_portion) + val_cut_off)
    # add up to 5331

    # split based on proportion
    data['X_val'] = pos_sens['sentences'][:val_cut_off] + neg_sens['sentences'][:val_cut_off]
    data['y_val'] = np.append(pos_sens['y'][:val_cut_off], neg_sens['y'][:val_cut_off])

    data['X_test'] = pos_sens['sentences'][val_cut_off:test_cut_off] + neg_sens['sentences'][val_cut_off:test_cut_off]
    data['y_test'] = np.append(pos_sens['y'][val_cut_off:test_cut_off], neg_sens['y'][val_cut_off:test_cut_off])

    # the rest, starting at test_cut_off, is all training data
    data['X_train'] = pos_sens['sentences'][test_cut_off:] + neg_sens['sentences'][test_cut_off:]
    data['y_train'] = np.append(pos_sens['y'][test_cut_off:], neg_sens['y'][test_cut_off:])

    return data


def get_data(category, file_path):
    """
    Args:
        category: 'pos', 'neg'
        data: pass in the dictionary, and we fill it up inside this function
    """

    data = {}

    data['sentences'] = []
    data['y'] = []

    with open(file_path, 'r') as f:
        for line in f:
            rev = []
            rev.append(line.strip())
            cleaned_sen = clean_str(" ".join(rev)).strip().split()
            data['sentences'].append(cleaned_sen)

            for word in cleaned_sen:
                if word not in word_idx_map:
                    word_idx_map[word] = len(word_idx_map)
                    idx_word_map.append(word)
                if word not in word_count_map:
                    word_count_map[word] = 0
                else:
                    word_count_map[word] += 1

            data['y'].append(int(label_idx_map[category]))

        # data['sentences'] = np.asarray(data['sentences'])
        data['y'] = np.asarray(data['y'], dtype='int32')

    return data


def decode(unicode_str):
    return unicodedata.normalize('NFKD', unicode_str).encode('ascii', 'ignore')


def convert_words_to_idx(data_X):
    """
    We convert word sentence into idx sentence,
    and if a word is not in word2vec: "rare", we already have a randomized word embedding

    Args:
        data_X: the 'train_sentences', 'dev_sentences', 'test_sentences'

    Returns:
    """
    converted = []

    for sen in data_X:
        sen_idx = []

        for word in sen:
            sen_idx.append(word_idx_map[word])

            sen_idx.append(2)  # append <END> token to it

        converted.append(sen_idx)

    return converted


def compress_word2vec(W_embed, model):
    """
    We compress word2vec's 1.5G file with
    only the words we have

    update W_embed

    word2vec: the word2vec model we loaded

    Returns:
    """

    num_words_not_in = 0

    for i, word in enumerate(idx_word_map):
        if word in model:
            W_embed[i, :] = model[word]
        else:
            num_words_not_in += 1

    print "words not in word2vec: ", num_words_not_in


def print_stats(threshold=1, display=20):
    """
    print out how many words are equal to or below threshold

    In SNLI we have 12534 rare words (that appeared only once) (almost 33% of the corpus)

    display: how many of such words we want to display
    """
    rare_words = []

    for k, v in word_count_map.iteritems():
        if v <= threshold:
            rare_words.append(k)

    print "total number of rare words are: ", len(rare_words)
    # words not in word2vec:  6636

    for i in xrange(display):
        print rare_words[i]


def clean_str(string, TREC=False):
    """
    Tokenization/string cleaning for all datasets except for SST.
    Every dataset is lower cased except for TREC

    (this removes "." period as well)
    """
    string = re.sub(r"[^A-Za-z0-9(),!?\'\`]", " ", string)
    string = re.sub(r"\'s", " \'s", string)
    string = re.sub(r"\'ve", " \'ve", string)
    string = re.sub(r"n\'t", " n\'t", string)
    string = re.sub(r"\'re", " \'re", string)
    string = re.sub(r"\'d", " \'d", string)
    string = re.sub(r"\'ll", " \'ll", string)
    string = re.sub(r",", " , ", string)
    string = re.sub(r"!", " ! ", string)
    string = re.sub(r"\(", " \( ", string)
    string = re.sub(r"\)", " \) ", string)
    string = re.sub(r"\?", " \? ", string)
    string = re.sub(r"\s{2,}", " ", string)
    return string.strip() if TREC else string.strip().lower()


if __name__ == '__main__':
    begin = time.time()

    pwd = os.path.dirname(os.path.realpath(__file__))

    model = Word2Vec.load_word2vec_format(pwd + '/GoogleNews-vectors-negative300.bin.gz', binary=True)

    print "word2vec loaded..."

    data = load_dataset(pwd)

    print "data loaded..."

    # initialize all embeddings randomly, then we swap out
    # words that appear in Word2Vec (unseen words are initialized randomly)

    W_embed = np.random.randn(len(idx_word_map), 300)

    W_embed /= 100

    compress_word2vec(W_embed, model)

    np.savez_compressed(pwd + "/rt_sentiment_processed", W_embed=W_embed,
                        word_idx_map=word_idx_map, idx_word_map=idx_word_map,
                        X_train=data['X_train'],
                        X_val=data['X_val'],
                        X_test=data['X_test'],
                        y_train=data['y_train'],
                        y_dev=data['y_dev'],
                        y_test=data['y_test'])

    end = time.time()

    print "time spent: ", (end - begin), "s"
    # 271.000173807 s
