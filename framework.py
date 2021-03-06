import tensorflow as tf
import numpy as np
import tensorflow.contrib.slim as slim
import datetime
import sys
from network.embedding import Embedding
from network.encoder import Encoder
from network.gcn import GCN
from network.selector import Selector
from network.classifier import Classifier
import os
import sklearn.metrics
import pickle as pkl
import networkx as nx
from scipy import sparse

import time

os.environ['CUDA_VISIBLE_DEVICES'] = '0'

FLAGS = tf.app.flags.FLAGS

class Accuracy(object):

    def __init__(self):
        self.correct = 0
        self.total = 0

    def add(self, is_correct):
        self.total += 1
        if is_correct:
            self.correct += 1

    def get(self):
        if self.total == 0:
            return 0
        else:
            return float(self.correct) / self.total

    def clear(self):
        self.correct = 0
        self.total = 0

class Framework(object):

    def __init__(self, is_training, use_bag=True):
        self.use_bag = use_bag
        self.is_training = is_training
        # Place Holder
        self.word = tf.placeholder(dtype=tf.int32, shape=[None, FLAGS.max_length], name='input_word')
        #self.word_vec = tf.placeholder(dtype=tf.float32, shape=[None, FLAGS.word_size], name='word_vec')
        self.pos1 = tf.placeholder(dtype=tf.int32, shape=[None, FLAGS.max_length], name='input_pos1')
        self.pos2 = tf.placeholder(dtype=tf.int32, shape=[None, FLAGS.max_length], name='input_pos2')
        self.length = tf.placeholder(dtype=tf.int32, shape=[None], name='input_length')
        self.mask = tf.placeholder(dtype=tf.int32, shape=[None, FLAGS.max_length], name='input_mask')
        self.label = tf.placeholder(dtype=tf.int32, shape=[None], name='label')
        self.label_for_select = tf.placeholder(dtype=tf.int32, shape=[None], name='label_for_select')
        self.scope = tf.placeholder(dtype=tf.int32, shape=[FLAGS.batch_size + 1], name='scope')
        self.weights = tf.placeholder(dtype=tf.float32, shape=[FLAGS.batch_size])
        self.data_word_vec = np.load(os.path.join(FLAGS.export_path, 'vec.npy'))
        # Gcn
        self.ent2id = tf.placeholder(dtype=tf.int32, name='ent2id')
        self.features = tf.sparse_placeholder(dtype=tf.float32, name='kg_features')
        adj_name = ['h2r_adj', 'r2t_adj', 'self_adj']
        self.supports = [tf.sparse_placeholder(dtype=tf.float32, name=adj_name[i]) for i in range(3)]
        self.gcn_dims = [100, 85, 70, 53]
        self.num_features_nonzero = tf.placeholder(tf.int32)

        # Network
        self.embedding = Embedding(is_training, self.data_word_vec, self.word, self.pos1, self.pos2)
        self.encoder = Encoder(is_training, FLAGS.drop_prob)
        self.gcn = GCN(is_training, FLAGS.gcn_drop_prob, FLAGS.num_classes, self.gcn_dims)
        self.selector = Selector(FLAGS.num_classes, is_training, FLAGS.drop_prob)
        self.classifier = Classifier(is_training, self.label, self.weights)


        # Metrics
        self.acc_NA = Accuracy()
        self.acc_not_NA = Accuracy()
        self.acc_total = Accuracy()
        self.step = 0

        # Session
        self.sess = None

    def load_train_data(self):
        print('reading training data...')
        #self.data_word_vec = np.load(os.path.join(FLAGS.export_path, 'vec.npy'))
        self.data_instance_triple = np.load(os.path.join(FLAGS.export_path, 'train_instance_triple.npy'))
        self.data_instance_scope = np.load(os.path.join(FLAGS.export_path, 'train_instance_scope.npy'))
        self.data_train_length = np.load(os.path.join(FLAGS.export_path, 'train_len.npy'))
        self.data_train_label = np.load(os.path.join(FLAGS.export_path, 'train_label.npy'))
        self.data_train_word = np.load(os.path.join(FLAGS.export_path, 'train_word.npy'))
        self.data_train_pos1 = np.load(os.path.join(FLAGS.export_path, 'train_pos1.npy'))
        self.data_train_pos2 = np.load(os.path.join(FLAGS.export_path, 'train_pos2.npy'))
        self.data_train_mask = np.load(os.path.join(FLAGS.export_path, 'train_mask.npy'))

        # gcn data
        self.load_gcn_data()

        print('reading finished')
        print('mentions         : %d' % (len(self.data_instance_triple)))
        print('sentences        : %d' % (len(self.data_train_length)))
        print('relations        : %d' % (FLAGS.num_classes))
        print('word size        : %d' % (FLAGS.word_size))
        print('position size     : %d' % (FLAGS.pos_size))
        print('hidden size        : %d' % (FLAGS.hidden_size))

        self.reltot = {}
        for index, i in enumerate(self.data_train_label):
            if not i in self.reltot:
                self.reltot[i] = 1.0
            else:
                self.reltot[i] += 1.0
        for i in self.reltot:
            self.reltot[i] = 1 / (self.reltot[i] ** (0.05))
        print(self.reltot)

    def load_test_data(self):
        print('reading test data...')
        #self.data_word_vec = np.load(os.path.join(FLAGS.export_path, 'vec.npy'))
        self.data_instance_entity = np.load(os.path.join(FLAGS.export_path, 'test_instance_entity.npy'))
        self.data_instance_entity_no_bag = np.load(os.path.join(FLAGS.export_path, 'test_instance_entity_no_bag.npy'))
        instance_triple = np.load(os.path.join(FLAGS.export_path, 'test_instance_triple.npy'))
        self.data_instance_triple = {}
        for item in instance_triple:
            self.data_instance_triple[(item[0], item[1], int(item[2]))] = 0
        self.data_instance_scope = np.load(os.path.join(FLAGS.export_path, 'test_instance_scope.npy'))
        self.data_test_length = np.load(os.path.join(FLAGS.export_path, 'test_len.npy'))
        self.data_test_label = np.load(os.path.join(FLAGS.export_path, 'test_label.npy'))
        self.data_test_word = np.load(os.path.join(FLAGS.export_path, 'test_word.npy'))
        self.data_test_pos1 = np.load(os.path.join(FLAGS.export_path, 'test_pos1.npy'))
        self.data_test_pos2 = np.load(os.path.join(FLAGS.export_path, 'test_pos2.npy'))
        self.data_test_mask = np.load(os.path.join(FLAGS.export_path, 'test_mask.npy'))

        # gcn data
        self.load_gcn_data()

        print('reading finished')
        print('mentions         : %d' % (len(self.data_instance_triple)))
        print('sentences        : %d' % (len(self.data_test_length)))
        print('relations        : %d' % (FLAGS.num_classes))
        print('word size        : %d' % (FLAGS.word_size))
        print('position size     : %d' % (FLAGS.pos_size))
        print('hidden size        : %d' % (FLAGS.hidden_size))

    def load_gcn_data(self):
        print('loading gcn features...')
        h2r_obj = open(os.path.join(FLAGS.export_path, 'h2r.graph'))
        self.load_h2r = pkl.load(h2r_obj)
        h2r_obj.close()
        r2t_obj = open(os.path.join(FLAGS.export_path, 'r2t.graph'))
        self.load_r2t = pkl.load(r2t_obj)
        r2t_obj.close()
        h2r_adj = nx.adjacency_matrix(self.load_h2r)
        r2t_adj = nx.adjacency_matrix(self.load_r2t)
        self_adj = sparse.csr_matrix(sparse.eye(h2r_adj.shape[0]))
        self.load_adjs = [h2r_adj, r2t_adj, self_adj]
        rela_embed = np.load(os.path.join(FLAGS.export_path, 'rela_embed.npy'))
        enty_embed = np.load(os.path.join(FLAGS.export_path, 'entity_embed.npy'))
        self.load_features = np.concatenate((enty_embed, rela_embed), axis=0)
        if self.is_training:
            self.load_ent2id = np.load(os.path.join(FLAGS.export_path, 'ent2id.npy'))
        else:
            self.load_ent2id = np.load(os.path.join(FLAGS.export_path, 'ent2id_test.npy'))
        # gcn data preprocess
        self.load_features, self.load_adjs = self.gcn.preprocess(self.load_features, self.load_adjs)

    def init_train_model(self, loss, output, optimizer=None, gcn_loss=None, gcn_optimizer=None):
        print('initializing training model...')

        # Loss and output
        self.loss = loss
        self.output = output

        # Optimizer
        config = tf.ConfigProto(allow_soft_placement=True)
        config.gpu_options.allow_growth = True
        self.sess = tf.Session(config=config)

        # optimizer
        self.global_step = tf.Variable(0, name='global_step', trainable=False)
        tf.summary.scalar('learning_rate', FLAGS.learning_rate)
        self.optimizer = optimizer(FLAGS.learning_rate)
        self.grads_and_vars = self.optimizer.compute_gradients(loss)
        self.train_op = self.optimizer.apply_gradients(self.grads_and_vars, global_step=self.global_step)

        # gcn op
        if (not gcn_loss is None) and (not gcn_optimizer is None):
            self.gcn_loss = gcn_loss
            self.gcn_optimizer = gcn_optimizer(FLAGS.gcn_learning_rate)
            self.gcn_train_op = self.gcn_optimizer.minimize(self.gcn_loss)

        # Summary
        self.merged_summary = tf.summary.merge_all()
        self.summary_writer = tf.summary.FileWriter(FLAGS.summary_dir, self.sess.graph)

        # Saver
        self.saver = tf.train.Saver(max_to_keep=None)
        if FLAGS.pretrain_model == "None":
            self.sess.run(tf.global_variables_initializer())
        else:
            self.saver.restore(self.sess, os.path.join(FLAGS.pretrain_dir, FLAGS.pretrain_model))

        print('initializing finished')


    def init_test_model(self, output):
        print('initializing test model...')
        self.output = output
        self.sess = tf.Session()
        self.saver = tf.train.Saver(max_to_keep=None)
        print('initializing finished')

    def train_one_step(self, index, scope, weights, label, ent_scope, result_needed=[]):
        
        feed_dict = {
            self.word: self.data_train_word[index, :],
            #self.word_vec: self.data_word_vec,
            self.pos1: self.data_train_pos1[index, :],
            self.pos2: self.data_train_pos2[index, :],
            self.mask: self.data_train_mask[index, :],
            self.length: self.data_train_length[index],
            self.label: label,
            self.label_for_select: self.data_train_label[index],
            self.scope: np.array(scope),
            self.weights: weights,
            # gcn placeholders
            self.features: self.load_features,
            self.num_features_nonzero: self.load_features[1].shape,
            self.ent2id: self.load_ent2id[ent_scope, :]
        }
        feed_dict.update({self.supports[i]: self.load_adjs[i] for i in range(3)})

        result = self.sess.run([self.train_op, self.global_step, self.merged_summary, self.output] + result_needed, feed_dict)
        self.step = result[1]
        # summary
        #self.summary_writer.add_summary(result[2], self.step)
        _output = result[3]
        result = result[4:]

        # Training accuracy
        for i, prediction in enumerate(_output):
            if label[i] == 0:
                self.acc_NA.add(prediction == label[i])
            else:
                self.acc_not_NA.add(prediction == label[i])
            self.acc_total.add(prediction == label[i])

        return result

    def test_one_step(self, index, scope, label, ent_scope, result_needed=[]):
        feed_dict = {
            self.word: self.data_test_word[index, :],
            #self.word_vec: self.data_word_vec,
            self.pos1: self.data_test_pos1[index, :],
            self.pos2: self.data_test_pos2[index, :],
            self.mask: self.data_test_mask[index, :],
            self.length: self.data_test_length[index],
            self.label: label,
            self.label_for_select: self.data_test_label[index],
            self.scope: np.array(scope),
            # gcn placeholders
            self.features: self.load_features,
            self.num_features_nonzero: self.load_features[1].shape,
            self.ent2id: self.load_ent2id[ent_scope, :]
        }
        feed_dict.update({self.supports[i]: self.load_adjs[i] for i in range(3)})
        result = self.sess.run([self.output] + result_needed, feed_dict)
        if self.use_bag:
            self.test_output = result[0]
        else:
            tmp_output = result[0]
            self.test_output = []
            for i in range(FLAGS.batch_size):
                self.test_output.append(np.mean(tmp_output[scope[i]:scope[i + 1]], axis=0))
        result = result[1:]

        return result

    def train(self, one_step=train_one_step):
        if not os.path.exists(FLAGS.checkpoint_dir):
            os.mkdir(FLAGS.checkpoint_dir)
        if self.use_bag:
            train_order = list(range(len(self.data_instance_triple)))
        else:
            train_order = list(range(len(self.data_train_word)))
        for epoch in range(FLAGS.max_epoch):
            print('epoch ' + str(epoch) + ' starts...')
            self.acc_NA.clear()
            self.acc_not_NA.clear()
            self.acc_total.clear()
            np.random.shuffle(train_order)
            for i in range(int(len(train_order) / float(FLAGS.batch_size))):
                if self.use_bag:
                    ent_scope = train_order[i * FLAGS.batch_size:(i + 1) * FLAGS.batch_size]
                    input_scope = np.take(self.data_instance_scope, ent_scope, axis=0)
                    index = []
                    scope = [0]
                    weights = []
                    label = []
                    for num in input_scope:
                        index = index + list(range(num[0], num[1] + 1))
                        label.append(self.data_train_label[num[0]])
                        scope.append(scope[len(scope) - 1] + num[1] - num[0] + 1)
                        weights.append(self.reltot[self.data_train_label[num[0]]])

                    loss = one_step(self, index, scope, weights, label, ent_scope, [self.loss])
                else:
                    index = range(i * FLAGS.batch_size, (i + 1) * FLAGS.batch_size)
                    weights = []
                    for i in index:
                        weights.append(self.reltot[self.data_train_label[i]])
                    loss = one_step(self, index, index + [0], weights, self.data_train_label[index], [self.loss])

                time_str = datetime.datetime.now().isoformat()
                sys.stdout.write("epoch %d step %d time %s | loss : %f, NA accuracy: %f, not NA accuracy: %f, total accuracy %f" % (epoch, i, time_str, loss[0], self.acc_NA.get(), self.acc_not_NA.get(), self.acc_total.get()) + '\n')
                sys.stdout.flush()

            if (epoch + 1) % FLAGS.save_epoch == 0:
                print('epoch ' + str(epoch + 1) + ' has finished')
                print('saving model...')
                path = self.saver.save(self.sess, os.path.join(FLAGS.checkpoint_dir, FLAGS.model_name), global_step=epoch)
                print('have saved model to ' + path)

    def test(self, one_step=test_one_step):
        epoch_range = eval(FLAGS.epoch_range)
        epoch_range = range(epoch_range[0], epoch_range[1])
        save_x = None
        save_y = None
        best_auc = 0
        best_epoch = 0
        print('test ' + FLAGS.model_name)
        for epoch in epoch_range:
            if not os.path.exists(os.path.join(FLAGS.checkpoint_dir, FLAGS.model_name + '-' + str(epoch) + '.index')):
                continue
            print('start testing checkpoint, iteration =', epoch)
            self.saver.restore(self.sess, os.path.join(FLAGS.checkpoint_dir, FLAGS.model_name + '-' + str(epoch)))
            stack_output = []
            stack_label = []
            total = int(len(self.data_instance_scope) / FLAGS.batch_size)

            test_result = []
            total_recall = 0
            for i in range(total):
                ent_scope = range(i * FLAGS.batch_size:min((i + 1) * FLAGS.batch_size, len(self.data_instance_scope)))
                input_scope = self.data_instance_scope[ent_scope]
                index = []
                scope = [0]
                label = []
                for num in input_scope:
                    index = index + list(range(num[0], num[1] + 1))
                    label.append(self.data_test_label[num[0]])
                    scope.append(scope[len(scope) - 1] + num[1] - num[0] + 1)

                one_step(self, index, scope, label, ent_scope, [])

                for j in range(len(self.test_output)):
                    pred = self.test_output[j]
                    entity = self.data_instance_entity[j + i * FLAGS.batch_size]
                    for rel in range(1, len(pred)):
                        flag = int(((entity[0], entity[1], rel) in self.data_instance_triple))
                        total_recall += flag
                        test_result.append([(entity[0], entity[1], rel), flag, pred[rel]])

                if i % 100 == 0:
                    sys.stdout.write('predicting {} / {}\n'.format(i, total))
                    sys.stdout.flush()

            print('\nevaluating...')

            sorted_test_result = sorted(test_result, key=lambda x: x[2])
            pr_result_x = []
            pr_result_y = []
            correct = 0
            for i, item in enumerate(sorted_test_result[::-1]):
                if item[1] == 1:
                    correct += 1
                pr_result_y.append(float(correct) / (i + 1))
                pr_result_x.append(float(correct) / total_recall)
                #if i > 5000:
                #    break

            auc = sklearn.metrics.auc(x=pr_result_x, y=pr_result_y)
            print('auc:', auc)
            if auc > best_auc:
                best_auc = auc
                best_epoch = epoch
                save_x = pr_result_x
                save_y = pr_result_y

        if not os.path.exists(FLAGS.test_result_dir):
            os.mkdir(FLAGS.test_result_dir)
        np.save(os.path.join(FLAGS.test_result_dir, FLAGS.model_name + '_x.npy'), save_x)
        np.save(os.path.join(FLAGS.test_result_dir, FLAGS.model_name + '_y.npy'), save_y)
        print('best epoch:', best_epoch)

    def adversarial(self, loss, embedding):
        perturb = tf.gradients(loss, embedding)
        perturb = tf.reshape((0.01 * tf.stop_gradient(tf.nn.l2_normalize(perturb, dim=[0, 1, 2]))), [-1, FLAGS.max_length, embedding.shape[-1]])
        embedding = embedding + perturb
        return embedding
