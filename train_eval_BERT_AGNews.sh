
#! /bin/sh

mkdir -p AGNews_model_save
mkdir -p AGNews_Lexicon
mkdir -p result
mkdir -p model_save
mkdir -p temp_data
mkdir -p data


python3.6 model_BERT/classifier_AGNews.py \
    --task mrpc \
    --mode train \
    --train_cfg ./model_BERT/config/train_mrpc.json \
    --model_cfg ./model_BERT/config/bert_base.json \
    --data_train_file total_data/agtrain.tsv \
    --data_test_file total_data/ag_test.tsv \
    --pretrain_file ./model_BERT/uncased_L-12_H-768_A-12/bert_model.ckpt \
    --vocab ./model_BERT/uncased_L-12_H-768_A-12/vocab.txt \
    --max_len 150
