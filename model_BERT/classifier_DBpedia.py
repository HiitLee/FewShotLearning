import itertools
import csv
import fire
import torch.nn.functional as F
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.metrics import roc_auc_score
import tokenization
import train
import random
import models
import optim
import checkpoint
import numpy as np
from utils import set_seeds, get_device, truncate_tokens_pair
import re
from nltk.corpus import stopwords 
from nltk.tokenize import word_tokenize 
from nltk.stem.wordnet import WordNetLemmatizer 
from torch.nn import functional
from torch import LongTensor
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence
import os
os.environ["CUDA_VISIBLE_DEVICES"] = '5'
class CsvDataset(Dataset):
    """ Dataset Class for CSV file """
    labels = None
    
    def __init__(self, file, pipeline=[]): # cvs file and pipeline object
        Dataset.__init__(self)
        data = []

        with open(file, "r", encoding='utf-8') as f:
            # list of splitted lines : line is also list
            lines = csv.reader(f, delimiter='\t')

            for instance in self.get_instances(lines): # instance : tuple of fields
                for proc in pipeline: # a bunch of pre-processing
                    
                    instance = proc(instance)
                data.append(instance)

        # To Tensors
   
        self.tensors = [torch.tensor(x, dtype=torch.long) for x in zip(*data)]
        
    def __len__(self):
        return self.tensors[0].size(0)

    def __getitem__(self, index):
        return [tensor[index] for tensor in self.tensors]

    def get_instances(self, lines):
        """ get instance array from (csv-separated) line list """
        raise NotImplementedError

            
class MRPC(CsvDataset):
    """ Dataset class for MRPC """
    labels = ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9" ,"10", "11", "12", "13", "14", "15") # label names
    def __init__(self, file, pipeline=[]):
        super().__init__(file, pipeline)

    def get_instances(self, lines):
        for line in itertools.islice(lines, 0, None): # skip header
            yield line[0], line[1].encode('utf8'),None # label, text_a, text_b

            

class MNLI(CsvDataset):
    """ Dataset class for MNLI """
    labels = ("contradiction", "entailment", "neutral") # label names
    def __init__(self, file, pipeline=[]):
        super().__init__(file, pipeline)

    def get_instances(self, lines):
        for line in itertools.islice(lines, 0, None): # skip header
            yield line[-1], line[8], line[9] # label, text_a, text_b


def dataset_class(task):
    """ Mapping from task string to Dataset Class """
    table = {'mrpc': MRPC, 'mnli': MNLI}
    return table[task]


class Pipeline():
    """ Preprocess Pipeline Class : callable """
    def __init__(self):
        super().__init__()

    def __call__(self, instance):
        raise NotImplementedError


class Tokenizing(Pipeline):
    """ Tokenizing sentence pair """
    def __init__(self, preprocessor, tokenize):
        super().__init__()
        self.preprocessor = preprocessor # e.g. text normalization
        self.tokenize = tokenize # tokenize function

    def __call__(self, instance):
        label, text_a, text_b = instance

        label = self.preprocessor(label)
        tokens_a = self.tokenize(self.preprocessor(text_a))
        tokens_b = self.tokenize(self.preprocessor(text_b)) \
                   if text_b else []

        return (label, tokens_a, tokens_b)

    

class AddSpecialTokensWithTruncation(Pipeline):
    """ Add special tokens [CLS], [SEP] with truncation """
    def __init__(self, max_len=512):
        super().__init__()
        self.max_len = max_len

    def __call__(self, instance):
        label, tokens_a, tokens_b  = instance

        #print(tokens_a)
        _max_len = self.max_len - 3 if tokens_b else self.max_len - 2
        truncate_tokens_pair(tokens_a, tokens_b, _max_len)
        # -3 special tokens for [CLS] text_a [SEP] text_b [SEP]
        # -2 special tokens for [CLS] text_a [SEP]
     
        
        # Add Special Tokens
        tokens_a = tokens_a 
        #print(label)

        return (label, tokens_a)


class TokenIndexing(Pipeline):
    """ Convert tokens into token indexes and do zero-padding """
    def __init__(self, indexer, labels, max_len=512):
        super().__init__()
        self.indexer = indexer # function : tokens to indexes
        # map from a label name to a label index
        self.label_map = {name: i for i, name in enumerate(labels)}
        self.max_len = max_len

    def __call__(self, instance):
        label, tokens_a = instance
        input_ids = self.indexer(tokens_a )
        seq_lengths = len(input_ids)
        segment_ids = [0]*len(tokens_a) # token type ids
        input_mask = [1]*len(tokens_a)
        label_id = self.label_map[label]
        # zero padding
        n_pad = self.max_len - len(input_ids)
        input_ids.extend([0]*n_pad)
        segment_ids.extend([0]*n_pad)
        input_mask.extend([0]*n_pad)
        
        return (input_ids, segment_ids, input_mask, label_id,seq_lengths)


'''
class Classifier_Attention_LSTM(nn.Module):
    """ Classifier with Transformer """
    def __init__(self,  n_labels):
        super().__init__()
        self.activ = nn.Tanh()
        self.classifier = nn.Linear(300, n_labels)
        self.rnn = nn.LSTM(300, 300, batch_first=True)
        self.softmax_word = nn.Softmax()

    def attention_net(self,lstm_output, final_state):
        hidden = final_state.squeeze(0)
        attn_weights = torch.bmm(lstm_output, hidden.unsqueeze(2)).squeeze(2)
        soft_attn_weights = F.softmax(attn_weights, 1)
        new_hidden_state = torch.bmm(lstm_output.transpose(1, 2), soft_attn_weights.unsqueeze(2)).squeeze(2)
        return new_hidden_state,soft_attn_weights

    def forward(self,token2, input_ids, segment_ids, input_mask,seq_lengths):
        packed_input = pack_padded_sequence(token2, seq_lengths.cpu().numpy(), batch_first=True)
        packed_output,(final_hidden_state, final_cell_state) = self.rnn(packed_input)
        r_output, input_sizes = pad_packed_sequence(packed_output, batch_first=True)
        attn_output,soft_attn_weights = self.attention_net(r_output, r_output[:,-1,:])
        logits = self.classifier(attn_output)
        return logits,soft_attn_weights
'''

class Classifier_Attention_LSTM(nn.Module):
    def __init__(self,  n_labels):
        super().__init__()
        self.rnn = nn.LSTM(300, 300, batch_first=True)
        self.tanh1 = nn.Tanh()
        self.w = nn.Parameter(torch.zeros(300))
        self.tanh2 = nn.Tanh()
        self.fc1 = nn.Linear(300,n_labels)

    def forward(self,token2, input_ids, segment_ids, input_mask,seq_lengths):
        packed_input = pack_padded_sequence(token2, seq_lengths.cpu().numpy(), batch_first=True)
        packed_output,(final_hidden_state, final_cell_state) = self.rnn(packed_input)
        r_output, input_sizes = pad_packed_sequence(packed_output, batch_first=True)
        output = r_output
        alpha = F.softmax(torch.matmul(output, self.w)).unsqueeze(-1)  # [48, 32, 1]

        out = r_output * alpha  # [48, 32, 256]
        out = torch.sum(out, 1)  # [48, 256]
        out = F.relu(out)
        out = self.fc1(out)
        return out, alpha.squeeze()

class Classifier_CNN(nn.Module):
    """ Classifier with Transformer """
    def __init__(self, n_labels):
        super().__init__()
        self.Lin1 = nn.Linear(100,n_labels)
        self.activ = nn.Tanh()
        self.drop = nn.Dropout(0.5)
        self.fc1 = nn.Linear(300, n_labels)  # a dense layer for classification
        self.convs_1d = nn.ModuleList([
            nn.Conv2d(1, 100, (k, 300), padding=[k-2,0]) 
            for k in [3,4,5]])
        
    def conv_and_pool(self, x, conv,x1):
        x = conv(x)
        x = F.relu(x)
        x = x.squeeze(3)
        x = F.max_pool1d(x, x.size(2)).squeeze(2)
        return x

    def forward(self, token2, input_ids, segment_ids, input_mask):
        embeds = token2.unsqueeze(1)
        conv_results = [self.conv_and_pool(embeds, conv,input_ids) for conv in self.convs_1d]
        x = torch.cat(conv_results, 1)
        logits = self.fc1(self.drop(x))
        return logits,logits 
    
class Classifier(nn.Module):
    """ Classifier with Transformer """
    def __init__(self, cfg, n_labels):
        super().__init__()
        self.transformer = models.Transformer(cfg)
        self.fc = nn.Linear(cfg.dim, cfg.dim)
        self.activ = nn.Tanh()
        self.drop = nn.Dropout(cfg.p_drop_hidden)
        self.classifier = nn.Linear(cfg.dim, n_labels)

    def forward(self, input_ids, segment_ids, input_mask):
        h = self.transformer(input_ids, segment_ids, input_mask)
        # only use the first h in the sequence
        pooled_h = self.activ(self.fc(h[:, 0]))
        logits = self.classifier(self.drop(pooled_h))
        return logits
    
    


def matching_blacklist2(abusive_set, input_sentence, temp):
    result_list = list()
    for i,abusive_word in enumerate(abusive_set):
        input_sentence2 = input_sentence.lower().split(' ')
        abusive_word2 = abusive_word.split(' ')
        flag=0
        for l in range(0, len(abusive_word2)):
            for input in input_sentence2:
                if abusive_word2[l].lower() in input:
                    if(len(abusive_word2[l]) >= len(input)-3):
                        #print(abusive_word2[l])
                        flag+=1
                        break
                        
        if(flag == temp):
            result_list.append(abusive_word)
                    
    return result_list


def main(task='mrpc',
         train_cfg='./model/config/train_mrpc.json',
         model_cfg='./model/config/bert_base.json',
         data_train_file='./total_data/dbtrain.tsv',
         data_test_file='./total_data/db_test.tsv',
         model_file=None,
         pretrain_file='./model/uncased_L-12_H-768_A-12/bert_model.ckpt',
         data_parallel=False,
         vocab='./model/uncased_L-12_H-768_A-12/vocab.txt',
         dataName='dbpedia',
         stopNum=1000,
         max_len=200,
         mode='train'):

    if mode == 'train':
        def get_loss_CNN(model, batch, global_step): # make sure loss is a scalar tensor
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            logits = model(input_ids, segment_ids, input_mask)
            loss = criterion(logits, label_id)
            return loss
        
        def evalute_CNN(model, batch):
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            logits = model(input_ids, segment_ids, input_mask)

            return label_id, logits
        
        
        def get_loss_Attn_LSTM(model, batch, global_step): # make sure loss is a scalar tensor
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            
            seq_lengths, perm_idx = seq_lengths.sort(0, descending=True)
            input_ids = input_ids[perm_idx]
            label_id = label_id[perm_idx]
            token1 = embedding(input_ids.long())
            
            logits,attention_score = model(token1.cuda(),input_ids, segment_ids, input_mask,seq_lengths)
            
            loss1 = criterion(logits, label_id)   
            return loss1
        
        
        def evalute_Attn_LSTM(model, batch,global_step,ls):
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            
            seq_lengths, perm_idx = seq_lengths.sort(0, descending=True)
            input_ids = input_ids[perm_idx]
            label_id = label_id[perm_idx]
            token1 = embedding(input_ids.long())
            
            
            logits,attention_score = model(token1.cuda(),input_ids, segment_ids, input_mask,seq_lengths)
            logits=F.softmax(logits)

            y_pred11, y_pred1 = logits.max(1)
            
         
            return label_id, logits
        
        def generating_lexiocn( model2, batch,global_step,ls, e):
            #print("global_step###:", global_step)
            if(global_step== 0):
                print("sdfafsafsaf###################")
                result3.clear()
                result_label.clear()
                bb_0.clear()
                bb_1.clear()
                bb_2.clear()
                bb_3.clear()
                bb_4.clear()
                bb_5.clear()
                bb_6.clear()
                bb_7.clear()
                bb_8.clear()
                bb_9.clear()
                bb_10.clear()
                bb_11.clear()
                bb_12.clear()
                bb_13.clear()
                bb_14.clear()
                bb_15.clear()

                
                    
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            
            seq_lengths, perm_idx = seq_lengths.sort(0, descending=True)
            input_ids = input_ids[perm_idx]
            label_id = label_id[perm_idx]
            token1 = embedding(input_ids.long())
            logits2,attention_score2 = model2(token1.cuda(),input_ids, segment_ids, input_mask,seq_lengths)

            logits=F.softmax(logits2)
            #logits2=F.softmax(logits2)
            y_pred22, y_pred2 = logits2.max(1)
            atten, attn_s1 = attention_score2.max(1)
            atte2, attn_s2 = torch.topk(attention_score2, 4)
            
            for i in range(0, len(input_ids)):
                split_tokens = []
                att_index=[]
                for token in tokenizer.tokenize(data0[global_step*48+perm_idx[i]]): 
                    split_tokens.append(token)
                
                if(len(split_tokens) <= attn_s1[i].item()):  
                    attn_index3 = attention_score2[i][:len(split_tokens)-1]
                    attn_num ,attn_index2 = attn_index3.max(0)
                    attn_index = attn_index2.item()
                else:
                    for j in range(0, 4):
                        att_index.append(attn_s2[i][j].item())
                    
                tok=[]
                if(atten[i].item()<= 0):
                    token_ab=split_tokens[0]
                else:
                    for j in range(0,len(att_index)):
                        if(att_index[j] >= len(split_tokens)):
                            continue
                        tok.append(split_tokens[att_index[j]])
                        
                token_temp = data0[global_step*48+perm_idx[i]].split(' ')
                token2 = []
                for kk in range(0,len(tok)):
                    token_ab = tok[kk]
                    #print("token_ab", token_ab)
                    token_ab=token_ab.replace(".", "")
                    token_ab=token_ab.replace(",", "")
                    token_ab=token_ab.replace("'", "")
                    token_ab=token_ab.replace("!", "")
                    token_ab=token_ab.replace("?", "")
                    token_ab=token_ab.replace("'", "")
                    token_ab=token_ab.replace('"', "")

                    for gge, input_word in enumerate(token_temp):
                        if(token_ab == '' or token_ab == ' ' or token_ab ==',' or token_ab=='.' or token_ab == 'from' or token_ab == 'are' or token_ab == 'is' or token_ab == 'and' or token_ab == 'with' or token_ab == 'may' or token_ab == 'would' or token_ab == 'could' or token_ab == 'have' or token_ab == 'has' or token_ab == 'had' or token_ab == 'was' or token_ab == 'were' or token_ab == 'this' or token_ab == 'who' or token_ab == 'that'):     
                            continue
                        if(len(token_ab) < 2):
                            continue
                        if(token_ab.lower() in input_word.lower()):
                            input_word=input_word.replace(".", "")
                            input_word=input_word.replace(",", "")
                            input_word=input_word.replace("'", "")
                            input_word=input_word.replace("!", "")
                            input_word=input_word.replace("?", "")
                            input_word=input_word.replace("'", "")
                            input_word=input_word.replace('"', "")

                            token2.append(input_word.lower())
                            break
                token2 = list(set(token2))
                
                if(len(token2) < 3):
                    continue
                    
                sen=""
                for l in range(0, len(token2)-1):
                    sen+=token2[l]+' '
                sen+=token2[len(token2)-1]
                    
                if(y_pred2[i]==0):
                    try:
                        bb_0[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_0[sen]=y_pred22[i].item()
                           

                if(y_pred2[i]==1):
                    try:
                        bb_1[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_1[sen]=y_pred22[i].item()


                if(y_pred2[i]==2):
                    try:
                        bb_2[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_2[sen]=y_pred22[i].item()


                if(y_pred2[i]==3 ):
                    try:
                        bb_3[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_3[sen]=y_pred22[i].item()
                
                
                if(y_pred2[i]==4 ):
                    try:
                        bb_4[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_4[sen]=y_pred22[i].item()


                if(y_pred2[i]==5 ):
                    try:
                        bb_5[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_5[sen]=y_pred22[i].item()
                
                if(y_pred2[i]==6 ):
                    try:
                        bb_6[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_6[sen]=y_pred22[i].item()
                
                if(y_pred2[i]==7 ):
                    try:
                        bb_7[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_7[sen]=y_pred22[i].item()

                
                if(y_pred2[i]==8 ):
                    try:
                        bb_8[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_8[sen]=y_pred22[i].item()
                
                if(y_pred2[i]==9 ):
                    try:
                        bb_9[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_9[sen]=y_pred22[i].item()
                        
                if(y_pred2[i]==10 ):
                    try:
                        bb_10[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_10[sen]=y_pred22[i].item()
                        
                if(y_pred2[i]==11 ):
                    try:
                        bb_11[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_11[sen]=y_pred22[i].item()
                        
                if(y_pred2[i]==12 ):
                    try:
                        bb_12[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_12[sen]=y_pred22[i].item()
                        
                if(y_pred2[i]==13 ):
                    try:
                        bb_13[sen]+=y_pred22[i].item()
                    except KeyError:
                        bb_13[sen]=y_pred22[i].item()
              
            
            if(global_step==ls-1):
                
                abusive_0.clear()
                abusive_1.clear()
                abusive_2.clear()
                abusive_3.clear()
                abusive_4.clear()
                abusive_5.clear()
                abusive_6.clear()
                abusive_7.clear()
                abusive_8.clear()
                abusive_9.clear()
                abusive_10.clear()
                abusive_11.clear()
                abusive_12.clear()
                abusive_13.clear()
             
                            
                bb_0_up = sorted(bb_0.items(),key=lambda x: x[1], reverse=True)
                bb_1_up = sorted(bb_1.items(),key=lambda x: x[1], reverse=True)
                bb_2_up = sorted(bb_2.items(),key=lambda x: x[1], reverse=True)
                bb_3_up = sorted(bb_3.items(),key=lambda x: x[1], reverse=True)
                bb_4_up = sorted(bb_4.items(),key=lambda x: x[1], reverse=True)
                bb_5_up = sorted(bb_5.items(),key=lambda x: x[1], reverse=True)
                bb_6_up = sorted(bb_6.items(),key=lambda x: x[1], reverse=True)
                bb_7_up = sorted(bb_7.items(),key=lambda x: x[1], reverse=True)
                bb_8_up = sorted(bb_8.items(),key=lambda x: x[1], reverse=True)
                bb_9_up = sorted(bb_9.items(),key=lambda x: x[1], reverse=True)
                
                bb_10_up = sorted(bb_10.items(),key=lambda x: x[1], reverse=True)
                bb_11_up = sorted(bb_11.items(),key=lambda x: x[1], reverse=True)
                bb_12_up = sorted(bb_12.items(),key=lambda x: x[1], reverse=True)
                bb_13_up = sorted(bb_13.items(),key=lambda x: x[1], reverse=True)
             
                
                lexicon_size = 50
                bb_0_up = bb_0_up[:lexicon_size]
                bb_1_up = bb_1_up[:lexicon_size]
                bb_2_up = bb_2_up[:lexicon_size]
                bb_3_up = bb_3_up[:lexicon_size]
                bb_4_up = bb_4_up[:lexicon_size]
                bb_5_up = bb_5_up[:lexicon_size]
                bb_6_up = bb_6_up[:lexicon_size]
                bb_7_up = bb_7_up[:lexicon_size]
                bb_8_up = bb_8_up[:lexicon_size]
                bb_9_up = bb_9_up[:lexicon_size]
                bb_10_up = bb_10_up[:lexicon_size]
                bb_11_up = bb_11_up[:lexicon_size]
                bb_12_up = bb_12_up[:lexicon_size]
                bb_13_up = bb_13_up[:lexicon_size]
              

                for i in bb_0_up:
                    abusive_0.append(i[0])
                for i in bb_1_up:
                    abusive_1.append(i[0])
                for i in bb_2_up:
                    abusive_2.append(i[0])
                for i in bb_3_up:
                    abusive_3.append(i[0])
                for i in bb_4_up:
                    abusive_4.append(i[0])
                for i in bb_5_up:
                    abusive_5.append(i[0])
                for i in bb_6_up:
                    abusive_6.append(i[0])
                for i in bb_7_up:
                    abusive_7.append(i[0])
                for i in bb_8_up:
                    abusive_8.append(i[0])
                for i in bb_9_up:
                    abusive_9.append(i[0])
                for i in bb_10_up:
                    abusive_10.append(i[0])
                for i in bb_11_up:
                    abusive_11.append(i[0])
                for i in bb_12_up:
                    abusive_12.append(i[0])
                for i in bb_13_up:
                    abusive_13.append(i[0])
              
          
                    
                    
                ddf = open("./DBpedia_Lexicon/dbLexicon_0.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_0)):
                    ddf.write(abusive_0[i]+'\n')
                ddf.close()

                ddf = open("./DBpedia_Lexicon/dbLexicon_1.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_1)):
                    ddf.write(abusive_1[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_2.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_2)):
                    ddf.write(abusive_2[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_3.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_3)):
                    ddf.write(abusive_3[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_4.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_4)):
                    ddf.write(abusive_4[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_5.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_5)):
                    ddf.write(abusive_5[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_6.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_6)):
                    ddf.write(abusive_6[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_7.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_7)):
                    ddf.write(abusive_7[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_8.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_8)):
                    ddf.write(abusive_8[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_9.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_9)):
                    ddf.write(abusive_9[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_10.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_10)):
                    ddf.write(abusive_10[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_11.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_11)):
                    ddf.write(abusive_11[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_12.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_12)):
                    ddf.write(abusive_12[i]+'\n')
                ddf.close()
                
                ddf = open("./DBpedia_Lexicon/dbLexicon_13.txt",'w', encoding='UTF8')
                for i in range(0, len(abusive_13)):
                    ddf.write(abusive_13[i]+'\n')
                ddf.close()
            
            return label_id, logits
        
       
        def evalute_CNN_SSL(model, batch,global_step):
            if(global_step== 0):
                result5.clear()
                
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            logits = model(input_ids, segment_ids, input_mask)
            logits=F.softmax(logits)
            y_pred11, y_pred1 = logits.max(1)

            for i in range(0, len(input_ids)):
                result5.append([y_pred1[i].item(), y_pred11[i].item()])

            return label_id, logits
        
        def pseudo_labeling(model2, batch, global_step,ls,e):
            #print("global_step###:", global_step)
            if(global_step== 0):
                result3.clear()
                result4.clear()

                label_0.clear()
                label_1.clear()
                label_2.clear()
                label_3.clear()
                label_4.clear()
                label_5.clear()
                label_6.clear()
                label_7.clear()
                label_8.clear()
                label_9.clear()
                
                label_10.clear()
                label_11.clear()
                label_12.clear()
                label_13.clear()
                label_14.clear()
                label_15.clear()
                
                result_label.clear()

                abusive_0.clear()
                abusive_1.clear()
                abusive_2.clear()
                abusive_3.clear()
                abusive_4.clear()
                abusive_5.clear()
                abusive_6.clear()
                abusive_7.clear()
                abusive_8.clear()
                abusive_9.clear()
                abusive_10.clear()
                abusive_11.clear()
                abusive_12.clear()
                abusive_13.clear()
                abusive_14.clear()
                abusive_15.clear()
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_0.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_0.append(line) 
                abusive_dic_file.close()
           
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_1.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_1.append(line) 
                abusive_dic_file.close()
                
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_2.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_2.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_3.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_3.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_4.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_4.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_5.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_5.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_6.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_6.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_7.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_7.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_8.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_8.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_9.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_9.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_10.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_10.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_11.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_11.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_12.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_12.append(line) 
                abusive_dic_file.close()
                
                abusive_dic_file = open("./DBpedia_Lexicon/dbLexicon_13.txt",'r', encoding='UTF8')              
                for line in abusive_dic_file.read().split('\n'):
                    if(len(line)<=3):
                        continue
                    abusive_13.append(line) 
                abusive_dic_file.close()
                
          
           
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            
            seq_lengths, perm_idx = seq_lengths.sort(0, descending=True)
            input_ids = input_ids[perm_idx]
            label_id = label_id[perm_idx]
            token1 = embedding(input_ids.long())
            
            
            logits2,attention_score2 = model2(token1.cuda(),input_ids, segment_ids, input_mask,seq_lengths)
            
            


            logits2=F.softmax(logits2)

 
            y_pred22, y_pred2 = logits2.max(1)

            label_id2=[]
 
            for i in range(0, len(input_ids)):
                input_sentence = data0[global_step*48+perm_idx[i]]
                input_sentence =re.sub("[!@#$%^&*().?\"~/<>:;'{}]","",input_sentence)
                matching_number=3
                abusive_word_list_neg0 = list()
                abusive_word_list_neg0 += matching_blacklist2(abusive_0, input_sentence,matching_number)
                abusive_word_list_neg0 = list((set(abusive_word_list_neg0)))
               
                abusive_word_list_neg1 = list()
                abusive_word_list_neg1 += matching_blacklist2(abusive_1, input_sentence,matching_number)
                abusive_word_list_neg1 = list((set(abusive_word_list_neg1)))
                
                abusive_word_list_neg2 = list()
                abusive_word_list_neg2 += matching_blacklist2(abusive_2, input_sentence,matching_number)
                abusive_word_list_neg2 = list((set(abusive_word_list_neg2)))
                
                abusive_word_list_neg3 = list()
                abusive_word_list_neg3 += matching_blacklist2(abusive_3, input_sentence,matching_number)
                abusive_word_list_neg3 = list((set(abusive_word_list_neg3)))
                
                abusive_word_list_neg4 = list()
                abusive_word_list_neg4 += matching_blacklist2(abusive_4, input_sentence,matching_number)
                abusive_word_list_neg4 = list((set(abusive_word_list_neg4)))
                
                abusive_word_list_neg5 = list()
                abusive_word_list_neg5 += matching_blacklist2(abusive_5, input_sentence,matching_number)
                abusive_word_list_neg5 = list((set(abusive_word_list_neg5)))
                
                abusive_word_list_neg6 = list()
                abusive_word_list_neg6 += matching_blacklist2(abusive_6, input_sentence,matching_number)
                abusive_word_list_neg6 = list((set(abusive_word_list_neg6)))
                
                abusive_word_list_neg7 = list()
                abusive_word_list_neg7 += matching_blacklist2(abusive_7, input_sentence,matching_number)
                abusive_word_list_neg7 = list((set(abusive_word_list_neg7)))
                
                
                abusive_word_list_neg8 = list()
                abusive_word_list_neg8 += matching_blacklist2(abusive_8, input_sentence,matching_number)
                abusive_word_list_neg8 = list((set(abusive_word_list_neg8)))
                
                abusive_word_list_neg9 = list()
                abusive_word_list_neg9 += matching_blacklist2(abusive_9, input_sentence,matching_number)
                abusive_word_list_neg9 = list((set(abusive_word_list_neg9)))
                
                
                
                abusive_word_list_neg10 = list()
                abusive_word_list_neg10 += matching_blacklist2(abusive_10, input_sentence,matching_number)
                abusive_word_list_neg10 = list((set(abusive_word_list_neg10)))
                
                abusive_word_list_neg11 = list()
                abusive_word_list_neg11 += matching_blacklist2(abusive_11, input_sentence,matching_number)
                abusive_word_list_neg11 = list((set(abusive_word_list_neg11)))
                
                abusive_word_list_neg12 = list()
                abusive_word_list_neg12 += matching_blacklist2(abusive_12, input_sentence,matching_number)
                abusive_word_list_neg12 = list((set(abusive_word_list_neg12)))
                
                abusive_word_list_neg13 = list()
                abusive_word_list_neg13 += matching_blacklist2(abusive_13, input_sentence,matching_number)
                abusive_word_list_neg13 = list((set(abusive_word_list_neg13)))
                

                matching_number2=4
                abusive_word_list_neg000 = list()
                abusive_word_list_neg000 += matching_blacklist2(abusive_0, input_sentence,matching_number2)
                abusive_word_list_neg000 = list((set(abusive_word_list_neg000)))
                
                abusive_word_list_neg111 = list()
                abusive_word_list_neg111 += matching_blacklist2(abusive_1, input_sentence,matching_number2)
                abusive_word_list_neg111 = list((set(abusive_word_list_neg111)))
                
                abusive_word_list_neg222 = list()
                abusive_word_list_neg222 += matching_blacklist2(abusive_2, input_sentence,matching_number2)
                abusive_word_list_neg222 = list((set(abusive_word_list_neg222)))
                
                abusive_word_list_neg333 = list()
                abusive_word_list_neg333 += matching_blacklist2(abusive_3, input_sentence,matching_number2)
                abusive_word_list_neg333 = list((set(abusive_word_list_neg333)))
                
                abusive_word_list_neg444 = list()
                abusive_word_list_neg444 += matching_blacklist2(abusive_4, input_sentence,matching_number2)
                abusive_word_list_neg444 = list((set(abusive_word_list_neg444)))
                
                abusive_word_list_neg555 = list()
                abusive_word_list_neg555 += matching_blacklist2(abusive_5, input_sentence,matching_number2)
                abusive_word_list_neg555 = list((set(abusive_word_list_neg555)))
                
                abusive_word_list_neg666 = list()
                abusive_word_list_neg666 += matching_blacklist2(abusive_6, input_sentence,matching_number2)
                abusive_word_list_neg666 = list((set(abusive_word_list_neg666)))
                
                abusive_word_list_neg777 = list()
                abusive_word_list_neg777 += matching_blacklist2(abusive_7, input_sentence,matching_number2)
                abusive_word_list_neg777 = list((set(abusive_word_list_neg777)))
                
                abusive_word_list_neg888 = list()
                abusive_word_list_neg888 += matching_blacklist2(abusive_8, input_sentence,matching_number2)
                abusive_word_list_neg888 = list((set(abusive_word_list_neg888)))
                
                abusive_word_list_neg999 = list()
                abusive_word_list_neg999 += matching_blacklist2(abusive_9, input_sentence,matching_number2)
                abusive_word_list_neg999 = list((set(abusive_word_list_neg999)))
                
                
                abusive_word_list_neg1010 = list()
                abusive_word_list_neg1010 += matching_blacklist2(abusive_10, input_sentence,matching_number2)
                abusive_word_list_neg1010 = list((set(abusive_word_list_neg1010)))
                
                abusive_word_list_neg1111 = list()
                abusive_word_list_neg1111 += matching_blacklist2(abusive_11, input_sentence,matching_number2)
                abusive_word_list_neg1111 = list((set(abusive_word_list_neg1111)))
                
                abusive_word_list_neg1212 = list()
                abusive_word_list_neg1212 += matching_blacklist2(abusive_12, input_sentence,matching_number2)
                abusive_word_list_neg1212 = list((set(abusive_word_list_neg1212)))
                
                abusive_word_list_neg1313 = list()
                abusive_word_list_neg1313 += matching_blacklist2(abusive_13, input_sentence,matching_number2)
                abusive_word_list_neg1313 = list((set(abusive_word_list_neg1313)))
                
               
                
            
                
                a = max(len(abusive_word_list_neg0), len(abusive_word_list_neg1), len(abusive_word_list_neg2), len(abusive_word_list_neg3), len(abusive_word_list_neg4), len(abusive_word_list_neg5), len(abusive_word_list_neg6), len(abusive_word_list_neg7), len(abusive_word_list_neg8), len(abusive_word_list_neg9), len(abusive_word_list_neg10), len(abusive_word_list_neg11), len(abusive_word_list_neg12), len(abusive_word_list_neg13))
                
                aa =max(len(abusive_word_list_neg000), len(abusive_word_list_neg111), len(abusive_word_list_neg222), len(abusive_word_list_neg333), len(abusive_word_list_neg444), len(abusive_word_list_neg555), len(abusive_word_list_neg666), len(abusive_word_list_neg777), len(abusive_word_list_neg888), len(abusive_word_list_neg999) , len(abusive_word_list_neg1010), len(abusive_word_list_neg1111), len(abusive_word_list_neg1212), len(abusive_word_list_neg1313))
                
                
                gg_a=0
                if(len(abusive_word_list_neg0)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg1)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg2)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg3)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg4)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg5)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg6)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg7)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg8)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg9)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg10)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg11)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg12)<a):
                    gg_a+=1
                if(len(abusive_word_list_neg13)<a):
                    gg_a+=1
             
                gg_a1=0
                if(len(abusive_word_list_neg000)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg111)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg222)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg333)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg444)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg555)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg666)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg777)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg888)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg999)<aa):
                    gg_a1+=1
                if(len(abusive_word_list_neg1010)<a):
                    gg_a1+=1
                if(len(abusive_word_list_neg1111)<a):
                    gg_a1+=1
                if(len(abusive_word_list_neg1212)<a):
                    gg_a1+=1
                if(len(abusive_word_list_neg1313)<a):
                    gg_a1+=1
             
             
               
                if((a>=1 and gg_a==13 and a == len(abusive_word_list_neg0) and result5[global_step*48+perm_idx[i]][0]==0  and result5[global_step*48+perm_idx[i]][1]>=0.9) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg0) and y_pred2[i].item()==0  and y_pred22[i].item()>=0.9)):
                    label_0.append(0)
                    result4.append([global_step*48+perm_idx[i],0,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg1) and result5[global_step*48+perm_idx[i]][0]==1 and result5[global_step*48+perm_idx[i]][1]>=0.9 ) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg1) and y_pred2[i].item()==1  and y_pred22[i].item()>=0.9)):
                    label_1.append(1)
                    result4.append([global_step*48+perm_idx[i],1,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg2) and result5[global_step*48+perm_idx[i]][0]==2 and result5[global_step*48+perm_idx[i]][1]>=0.9 ) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg2) and y_pred2[i].item()==2  and y_pred22[i].item()>=0.9)):
                    label_2.append(2)
                    result4.append([global_step*48+perm_idx[i],2,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg3) and result5[global_step*48+perm_idx[i]][0]==3 and result5[global_step*48+perm_idx[i]][1]>=0.9 ) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg3) and y_pred2[i].item()==3  and y_pred22[i].item()>=0.9)):
                    label_3.append(3)
                    result4.append([global_step*48+perm_idx[i],3,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                    
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg4) and result5[global_step*48+perm_idx[i]][0]==4 and result5[global_step*48+perm_idx[i]][1]>=0.9) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg4) and y_pred2[i].item()==4  and y_pred22[i].item()>=0.9)):
                    label_4.append(4)
                    result4.append([global_step*48+perm_idx[i],4,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg5) and result5[global_step*48+perm_idx[i]][0]==5 and result5[global_step*48+perm_idx[i]][1]>=0.9 ) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg5) and y_pred2[i].item()==5  and y_pred22[i].item()>=0.9)):
                    label_5.append(5)
                    result4.append([global_step*48+perm_idx[i],5,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg6) and result5[global_step*48+perm_idx[i]][0]==6  and result5[global_step*48+perm_idx[i]][1]>=0.9) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg6) and y_pred2[i].item()==6  and y_pred22[i].item()>=0.9)):
                    label_6.append(6)
                    result4.append([global_step*48+perm_idx[i],6,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg7) and result5[global_step*48+perm_idx[i]][0]==7  and result5[global_step*48+perm_idx[i]][1]>=0.9) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg7) and y_pred2[i].item()==7 and y_pred22[i].item()>=0.9)):
                    label_7.append(7)
                    result4.append([global_step*48+perm_idx[i],7,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg8) and result5[global_step*48+perm_idx[i]][0]==8  and result5[global_step*48+perm_idx[i]][1]>=0.9) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg8) and y_pred2[i].item()==8  and y_pred22[i].item()>=0.9)):
                    label_8.append(8)
                    result4.append([global_step*48+perm_idx[i],8,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg9) and result5[global_step*48+perm_idx[i]][0]==9  and result5[global_step*48+perm_idx[i]][1]>=0.9) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg9) and y_pred2[i].item()==9 and y_pred22[i].item()>=0.9)):
                    label_9.append(9)
                    result4.append([global_step*48+perm_idx[i],9,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg10) and result5[global_step*48+perm_idx[i]][0]==10 and result5[global_step*48+perm_idx[i]][1]>=0.9 ) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg10) and y_pred2[i].item()==10 and y_pred22[i].item()>=0.9)):
                    label_10.append(9)
                    result4.append([global_step*48+perm_idx[i],10,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg11) and result5[global_step*48+perm_idx[i]][0]==11  and result5[global_step*48+perm_idx[i]][1]>=0.9) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg11) and y_pred2[i].item()==11 and y_pred22[i].item()>=0.9)):
                    label_11.append(9)
                    result4.append([global_step*48+perm_idx[i],11,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg12) and result5[global_step*48+perm_idx[i]][0]==12  and result5[global_step*48+perm_idx[i]][1]>=0.9) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg12) and y_pred2[i].item()==12  and y_pred22[i].item()>=0.9)):
                    label_12.append(9)
                    result4.append([global_step*48+perm_idx[i],12,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif((a>=1 and gg_a==13 and a == len(abusive_word_list_neg13) and result5[global_step*48+perm_idx[i]][0]==13  and result5[global_step*48+perm_idx[i]][1]>=0.9) or (a>=1 and gg_a==13 and a == len(abusive_word_list_neg13) and y_pred2[i].item()==13  and y_pred22[i].item()>=0.9)):
                    label_13.append(9)
                    result4.append([global_step*48+perm_idx[i],13,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()]) 
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg000)):
                    label_0.append(0)
                    result4.append([global_step*48+perm_idx[i],0,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg111)):
                    label_1.append(1)
                    result4.append([global_step*48+perm_idx[i],1,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg222)):
                    label_2.append(2)
                    result4.append([global_step*48+perm_idx[i],2,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg333)):
                    label_3.append(3)
                    result4.append([global_step*48+perm_idx[i],3,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg444)):
                    label_4.append(4)
                    result4.append([global_step*48+perm_idx[i],4,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg555)):
                    label_5.append(5)
                    result4.append([global_step*48+perm_idx[i],5,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg666)):
                    label_6.append(6)
                    result4.append([global_step*48+perm_idx[i],6,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg777)):
                    label_7.append(7)
                    result4.append([global_step*48+perm_idx[i],7,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg888)):
                    label_8.append(8)
                    result4.append([global_step*48+perm_idx[i],8,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg999)):
                    label_9.append(9)
                    result4.append([global_step*48+perm_idx[i],9,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg1010)):
                    label_10.append(9)
                    result4.append([global_step*48+perm_idx[i],10,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg1111)):
                    label_11.append(9)
                    result4.append([global_step*48+perm_idx[i],11,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg1212)):
                    label_12.append(9)
                    result4.append([global_step*48+perm_idx[i],12,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(aa>=1 and gg_a1==13 and aa == len(abusive_word_list_neg1313)):
                    label_13.append(9)
                    result4.append([global_step*48+perm_idx[i],13,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                elif(result5[global_step*48+perm_idx[i]][1]>=0.9 and y_pred22[i].item()>=0.9 and result5[global_step*48+perm_idx[i]][0]==y_pred2[i].item()):
                    if(result5[global_step*48+perm_idx[i]][0]==0):
                        label_0.append(0)
                        result4.append([global_step*48+perm_idx[i],0,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==1):
                        label_1.append(1)
                        result4.append([global_step*48+perm_idx[i],1,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==2):
                        label_2.append(2)
                        result4.append([global_step*48+perm_idx[i],2,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==3):
                        label_3.append(3)
                        result4.append([global_step*48+perm_idx[i],3,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==4):
                        label_4.append(4)
                        result4.append([global_step*48+perm_idx[i],4,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==5):
                        label_5.append(5)
                        result4.append([global_step*48+perm_idx[i],5,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==6):
                        label_6.append(6)
                        result4.append([global_step*48+perm_idx[i],6,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==7):
                        label_7.append(7)
                        result4.append([global_step*48+perm_idx[i],7,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==8):
                        label_8.append(8)
                        result4.append([global_step*48+perm_idx[i],8,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==9):
                        label_9.append(9)
                        result4.append([global_step*48+perm_idx[i],9,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==10):
                        label_10.append(9)
                        result4.append([global_step*48+perm_idx[i],10,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==11):
                        label_11.append(9)
                        result4.append([global_step*48+perm_idx[i],11,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==12):
                        label_12.append(9)
                        result4.append([global_step*48+perm_idx[i],12,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    elif(result5[global_step*48+perm_idx[i]][0]==13):
                        label_13.append(9)
                        result4.append([global_step*48+perm_idx[i],13,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])
                    
            
                else:
                    result4.append([global_step*48+perm_idx[i],-1,data0[global_step*48+perm_idx[i]], label_id[perm_idx[i]].item()])



            if(global_step==ls-1):
                result_label.clear()
                result3.clear()
                a = min(len(label_0), len(label_1), len(label_2), len(label_3), len(label_4), len(label_5), len(label_6), len(label_7), len(label_8), len(label_9) , len(label_10), len(label_11), len(label_12), len(label_13))
                
              
                la_0=0
                la_1=0
                la_2=0
                la_3=0
                la_4=0
                la_5=0
                la_6=0
                la_7=0
                la_8=0
                la_9=0
                la_10=0
                la_11=0
                la_12=0
                la_13=0
                la_14=0
                la_15=0

                random.shuffle(result4)
                for i in range(0, len(result4)):

                    if(result4[i][1] == 0 and a > la_0):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 0
                            la_0+=1
                            continue

           
                    elif(result4[i][1] == 1 and a > la_1):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 1
                            la_1+=1
                            continue
               

                    elif(result4[i][1] == 2 and a > la_2):
                       
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 2
                            la_2+=1
                            continue

                    elif(result4[i][1] == 3 and a > la_3):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 3
                            la_3+=1
                            continue
                            
                    elif(result4[i][1] == 4 and a > la_4):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 4
                            la_4+=1
                            continue
                            
                    elif(result4[i][1] == 5 and a > la_5):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] =5
                            la_5+=1
                            continue
                            
                    elif(result4[i][1] == 6 and a > la_6):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 6
                            la_6+=1
                            continue
                            
                    elif(result4[i][1] == 7 and a > la_7):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 7
                            la_7+=1
                            continue
                            
                    elif(result4[i][1] == 8 and a > la_8):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 8
                            la_8+=1
                            continue
                            
                    elif(result4[i][1] == 9 and a > la_9):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 9
                            la_9+=1
                            continue
                    
                    
                    elif(result4[i][1] == 10 and a > la_10):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 10
                            la_10+=1
                            continue
                    
                    
                    elif(result4[i][1] == 11 and a > la_11):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 11
                            la_11+=1
                            continue
                    
                    
                    elif(result4[i][1] == 12 and a > la_12):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 12
                            la_12+=1
                            continue
                    
                    
                    elif(result4[i][1] == 13 and a > la_13):
                        if(temp_check[result4[i][0]][0] == 0):
                            temp_check[result4[i][0]][0]=1
                            temp_check[result4[i][0]][1] = 13
                            la_13+=1
                            continue
                    
                    
           
        
                
                result_label.clear()
                result3.clear()
                
                fw = open('./temp_data/temp_train_DBpedia.tsv', 'a', encoding='utf-8', newline='')
                wr = csv.writer(fw, delimiter='\t')
                
                fww = open('./temp_data/temp_train_na_DBpedia.tsv', 'w', encoding='utf-8', newline='')
                wrr = csv.writer(fww, delimiter='\t')
                



                for i in range(0, len(temp_check)):
                    if(temp_check[i][0] == 1):
                        result_label.append(str(temp_check[i][3]))
                        result3.append(str(temp_check[i][1]))
                        wr.writerow([str(temp_check[i][1]),str(temp_check[i][2])])
                    else:
                        wrr.writerow([str(temp_check[i][3]),str(temp_check[i][2])])
                        
                        
                fw.close()
                fww.close()
                
                data0.clear()
                temp_check.clear()
                with open('./temp_data/temp_train_na_DBpedia.tsv', "r", encoding='utf-8') as f:
                    lines = csv.reader(f, delimiter='\t')

                    for i in lines:
                        a=''
                        lines2 = i[1].split(' ')
                        b=0
                        for j in range(0, len(lines2)):
                            a+=lines2[j]+' '
                            b+=1

                        data0.append(a)
                        temp_check.append([0,-1,a,i[0]])
                print("################;" , len(data0))
                f.close() 

                dataset_temp = TaskDataset('./temp_data/temp_train_DBpedia.tsv', pipeline)
                data_iter_temp = DataLoader(dataset_temp, batch_size=48, shuffle=True)
                dataset_temp_b = TaskDataset('./temp_data/temp_train_DBpedia.tsv', pipeline1)
                data_iter_temp_b = DataLoader(dataset_temp_b, batch_size=48, shuffle=True)
                
                
                dataset_temp_na = TaskDataset('./temp_data/temp_train_na_DBpedia.tsv', pipeline)
                data_iter_temp_na = DataLoader(dataset_temp_na, batch_size=48, shuffle=False)
                dataset_temp_na_b = TaskDataset('./temp_data/temp_train_na_DBpedia.tsv', pipeline1)
                data_iter_temp_na_b = DataLoader(dataset_temp_na_b, batch_size=48, shuffle=False)
                


             
            if(global_step!=ls-1):
                data_iter_temp = 1
                data_iter_temp_b = 1
                data_iter_temp_na = 1
                data_iter_temp_na_b = 1
                

            return label_id, logits2, result_label,result3, data_iter_temp,data_iter_temp_b, data_iter_temp_na,data_iter_temp_na_b
        def evalute_Attn_LSTM_SSL(model, batch):
            
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            
            seq_lengths, perm_idx = seq_lengths.sort(0, descending=True)
            input_ids = input_ids[perm_idx]
            label_id = label_id[perm_idx]
            token1 = embedding(input_ids.long())
            
            
            logits,attention_score = model2(token1.cuda(),input_ids, segment_ids, input_mask,seq_lengths)

            return label_id, logits
        if(dataName == "IMDB"):
            labelNum = 2
            dataName= "IMDB"
            tdataName = "imdbtrain"
            testName = "IMDB_test"
            Dict2 = {
		    "0" : {},
		    "1" : {}
		    }
        elif(dataName == "AG"):
            labelNum = 4
            dataName = "AG"
            tdataName = "agtrain"
            testName = "ag_test"
            Dict2 = {
		    "0" : {},
		    "1" : {},
		    "2" : {},
		    "3" : {}
		    }
        elif(dataName == "yahoo"):
            labelNum = 10
            dataName = "yahoo"
            tdataName = "yahootrain"
            testName = "yahoo_test"
            Dict2 = {
		    "0" : {},
		    "1" : {},
		    "2" : {},
		    "3" : {},
		    "4" : {},
		    "5" : {},
		    "6" : {},
		    "7" : {},
		    "8" : {},
		    "9" : {}
		    }
        
        
        elif(dataName == "dbpedia"):
            labelNum = 14
            dataName == "dbpedia"
            tdataName = "dbtrain"
            testName = "db_test"
            Dict2 = {
		    "0" : {},
		    "1" : {},
		    "2" : {},
		    "3" : {},
		    "4" : {},
		    "5" : {},
		    "6" : {},
		    "7" : {},
		    "8" : {},
		    "9" : {},
		    "10" : {},
		    "11" : {},
		    "12" : {},
		    "13" : {}
		    }
        
        curNum=0
        cfg = train.Config.from_json(train_cfg)
        model_cfg = models.Config.from_json(model_cfg)


        for kkk in  range(0, 5):
        
           
            tokenizer = tokenization.FullTokenizer(do_lower_case=True)
            tokenizer1 = tokenization.FullTokenizer1(vocab_file=vocab, do_lower_case=True)

            TaskDataset = dataset_class(task) # task dataset class according to the task
            pipeline = [Tokenizing(tokenizer.convert_to_unicode, tokenizer.tokenize),
                        AddSpecialTokensWithTruncation(max_len),
                        TokenIndexing(tokenizer.convert_tokens_to_ids,
                                      TaskDataset.labels, max_len)]
            pipeline1 = [Tokenizing(tokenizer1.convert_to_unicode, tokenizer1.tokenize),
                AddSpecialTokensWithTruncation(max_len),
                TokenIndexing(tokenizer1.convert_tokens_to_ids1,
                                  TaskDataset.labels, max_len)]


            data_unlabeled_file = "./data/"+dataName + "_unlabeled" + str(kkk+1)+".tsv"
            data_dev_file = "./data/" + dataName + "_dev" + str(kkk+1)+".tsv"
            data_labeled_file = "./data/" + dataName + "_labeled" + str(kkk+1)+".tsv"
            data_total_file = "./total_data/" + tdataName + ".tsv"
            data_test_file = "./total_data/" + testName + ".tsv"
            f_total = open(data_total_file, 'r', encoding='utf-8')
            r_total = csv.reader(f_total, delimiter='\t')

            allD=[]
            for line in r_total:
                allD.append([line[0],line[1]])
            f_total.close()

            for ii in range(0, kkk+1):
                random.shuffle(allD)
            
#            num_data = 7010* labelNum
#            num_data_dev_temp = 2010 * labelNum
#            num_data_dev = 2000 * labelNum
#            num_data_labeled = 10 * labelNum
            #num_data_unlabeled = 200000 - num_data_dev_temp
#            num_data_unlabeled = num_data - num_data_dev_temp
#            
##            #num_data = 5010* labelNum
#            num_data_dev_temp = 20 * labelNum
#            num_data_dev = 10 * labelNum
#            num_data_labeled = 10 * labelNum
#            #num_data_unlabeled = 200000 - num_data_dev_temp
#            num_data_unlabeled = len(allD) - num_data_dev_temp
	    num_data = len(allD)
            num_data_dev_temp = int(int(num_data*0.01)/labelNum)
            num_data_dev = int(int(num_data_dev_temp*0.15)/labelNum)
            num_data_labeled = int(int(num_data_dev_temp*0.85)/labelNum)
            num_data_unlabeled = num_data - int(num_data_dev_temp*labelNum)
            
            print("num_data_dev#: ", num_data_dev)
            print("num_data_labeled#: ",num_data_labeled)
            print("num_data_unlabeled#: ",num_data_unlabeled)


            f_temp = open('./data/temp_data.tsv', 'w', encoding='utf-8', newline='')
            w_temp = csv.writer(f_temp, delimiter='\t')

            f_unlabeled = open(data_unlabeled_file, 'w', encoding='utf-8', newline='')
            w_unlabeled = csv.writer(f_unlabeled, delimiter='\t')
           
            allD2=[]
            tempD={}
            for line in allD:
                if(line[0] not in tempD):
                    allD2.append([line[0],line[1]])
                    tempD[line[0]] = 1
                elif(tempD[line[0]] <= int(num_data_dev_temp/labelNum)):
                    allD2.append([line[0],line[1]])
                    tempD[line[0]] += 1
                elif(tempD[line[0]] <= int(num_data_dev_temp/labelNum)+int(num_data_unlabeled/labelNum)):
                    allD2.append([line[0],line[1]])
                    tempD[line[0]] += 1

            tempD={}
            for line in allD2:
                if(line[0] not in tempD):
                    tempD[line[0]] = 1
                    w_temp.writerow([line[0],line[1]])
                elif(tempD[line[0]] <= int(num_data_dev_temp/labelNum)):
                    tempD[line[0]] += 1
                    w_temp.writerow([line[0],line[1]])
                elif(tempD[line[0]] <= int(num_data_dev_temp/labelNum)+int(num_data_unlabeled/labelNum)):
                    w_unlabeled.writerow([line[0],line[1]])
                    tempD[line[0]] += 1

            f_temp.close()
            f_unlabeled.close()                


            f_temp = open('./data/temp_data.tsv', 'r', encoding='utf-8')
            r_temp = csv.reader(f_temp, delimiter='\t')

            f_dev = open(data_dev_file, 'w', encoding='utf-8', newline='')
            w_dev = csv.writer(f_dev, delimiter='\t')

            f_labeled = open(data_labeled_file, 'w', encoding='utf-8', newline='')
            w_labeled = csv.writer(f_labeled, delimiter='\t')

            tempD={}
            for line in r_temp:
                if(line[0] not in tempD):
                    tempD[line[0]] = 1
                    w_dev.writerow([line[0],line[1]])
                elif(tempD[line[0]] <= (num_data_dev/labelNum)):
                    tempD[line[0]] += 1
                    w_dev.writerow([line[0],line[1]])
                else:
                    w_labeled.writerow([line[0],line[1]])
                
            f_temp.close()
            f_dev.close()
            f_labeled.close()
            
            

            

            dataset = TaskDataset(data_unlabeled_file, pipeline)
            data_iter = DataLoader(dataset, batch_size=48, shuffle=False)
            
            dataset_b = TaskDataset(data_unlabeled_file, pipeline1)
            data_iter_b = DataLoader(dataset_b, batch_size=48, shuffle=False)
            

            dataset2 = TaskDataset(data_test_file, pipeline)
            data_iter2 = DataLoader(dataset2, batch_size=48, shuffle=False)
            
            dataset2_b = TaskDataset(data_test_file, pipeline1)
            data_iter2_b = DataLoader(dataset2_b, batch_size=48, shuffle=False)
            


            dataset_dev = TaskDataset(data_dev_file, pipeline)
            data_iter_dev = DataLoader(dataset_dev, batch_size=48, shuffle=False)
            
            dataset_dev_b = TaskDataset(data_dev_file, pipeline1)
            data_iter_dev_b = DataLoader(dataset_dev_b, batch_size=48, shuffle=False)


            dataset3 = TaskDataset(data_labeled_file, pipeline)
            data_iter3 = DataLoader(dataset3, batch_size=48, shuffle=True)
            
            dataset3_b = TaskDataset(data_labeled_file, pipeline1)
            data_iter3_b = DataLoader(dataset3_b, batch_size=48, shuffle=True)


            weights = tokenization.embed_lookup2()

            print("#train_set:", len(data_iter))
            print("#test_set:", len(data_iter2))
            print("#short_set:", len(data_iter3))
            print("#dev_set:", len(data_iter_dev))



            embedding = nn.Embedding.from_pretrained(weights).cuda()
            criterion = nn.CrossEntropyLoss()
            curNum+=1


            model = Classifier(model_cfg, labelNum)
            model2 = Classifier_Attention_LSTM(labelNum)

            trainer = train.Trainer(cfg,
                                    dataName,
                                    stopNum,
                                    model,
                                    model2,
                                    data_iter,
                                    data_iter_b,
                                    data_iter2,
                                    data_iter2_b,
                                    data_iter3,
                                    data_iter3_b,
                                    data_iter_dev,
                                    data_iter_dev_b,
                                    optim.optim4GPU(cfg, model,len(data_iter)*10 ),
                                    torch.optim.Adam(model2.parameters(), lr=0.005),
                                    get_device(),kkk+1)



            label_0=[]
            label_1=[]
            label_2=[]
            label_3=[]
            label_4=[]
            label_5=[]
            label_6=[]
            label_7=[]
            label_8=[]
            label_9=[]
            label_10=[]
            label_11=[]
            label_12=[]
            label_13=[]
            label_14=[]
            label_15=[]


            result3=[]
            result4=[]
            result5=[]



            bb_0={}
            bb_1={}
            bb_2={}
            bb_3={}
            bb_4={}
            bb_5={}
            bb_6={}
            bb_7={}
            bb_8={}
            bb_9={}
            bb_10={}
            bb_11={}
            bb_12={}
            bb_13={}
            bb_14={}
            bb_15={}




            abusive_0=[]
            abusive_1=[]
            abusive_2=[]
            abusive_3=[]
            abusive_4=[]
            abusive_5=[]
            abusive_6=[]
            abusive_7=[]
            abusive_8=[]
            abusive_9=[]       
            abusive_10=[]
            abusive_11=[]
            abusive_12=[]
            abusive_13=[]
            abusive_14=[]
            abusive_15=[]


            result_label=[]



            result_label=[]


            fw = open('./temp_data/temp_train_DBpedia.tsv', 'w', encoding='utf-8', newline='')
            wr = csv.writer(fw, delimiter='\t')

            fr = open(data_labeled_file, 'r', encoding='utf-8')
            rdrr = csv.reader(fr,  delimiter='\t')
            for line in rdrr:
                wr.writerow([line[0],line[1]])

            fw.close()
            fr.close()

            data0=[]
            temp_check=[]
            temp_label=[]
            with open(data_unlabeled_file, "r", encoding='utf-8') as f:
                lines = csv.reader(f, delimiter='\t')
                for i in lines:
                    a=''
                    lines2 = i[1].split(' ')
                    b=0
                    for j in range(0, len(lines2)):
                        a+=lines2[j]+' '
                        b+=1

                    data0.append(a)
                    temp_check.append([0,-1,a,i[0]])
                    temp_label.append([0,0,0,0,0,0,0,0,0,0,0,0,0,0,0])
            f.close()   


            trainer.train(model_file, pretrain_file, get_loss_CNN, get_loss_Attn_LSTM,evalute_CNN_SSL,pseudo_labeling,evalute_Attn_LSTM,evalute_CNN,evalute_Attn_LSTM_SSL,generating_lexiocn, data_parallel)

    elif mode == 'eval':
        def evalute_Attn_LSTM_SSL(model, batch):
            
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            
            seq_lengths, perm_idx = seq_lengths.sort(0, descending=True)
            input_ids = input_ids[perm_idx]
            label_id = label_id[perm_idx]
            token1 = embedding(input_ids.long())
            
            
            logits,attention_score = model2(token1.cuda(),input_ids, segment_ids, input_mask,seq_lengths)

            return label_id, logits
        
        def evalute_CNN_SSL(model, batch):
            input_ids, segment_ids, input_mask, label_id,seq_lengths = batch
            token1 = embedding(input_ids.long())
            logits,attention_score = model(token1.cuda(),input_ids, segment_ids, input_mask)

            return label_id, logits

        weights = tokenization.embed_lookup2()
        
        embedding = nn.Embedding.from_pretrained(weights).cuda()
        criterion = nn.CrossEntropyLoss()


        model = Classifier_CNN(14)
        model2 = Classifier_Attention_LSTM(14)

        trainer = train.Eval(cfg,
                                model,
                                model2,
                                data_iter,
                                save_dir, get_device())

        embedding = nn.Embedding.from_pretrained(weights).cuda()
        results = trainer.eval(evalute_CNN_SSL, evalute_Attn_LSTM_SSL, data_parallel)
        #total_accuracy = torch.cat(results).mean().item()
        #print('Accuracy:', total_accuracy)


if __name__ == '__main__':
    fire.Fire(main)
