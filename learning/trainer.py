import tensorflow as tf
import numpy as np
import csv

from util import util

class Trainer(object):
    def __init__(self, model, model_checkpoint='checkpoints/last_cnn.ckpt', eval_steps=10000):
        self.model = model

        self.eval_steps_mod = eval_steps
        self.exec_name = 'train'
        self.running_losses = {}
        self.eval_losses = {}
        self.best_loss = 100  # INF
        self.model_checkpoint = model_checkpoint
        self.running_loss, self.running_steps = 0, 0

    def run_training(self, data, max_steps, eval=True, test=True, output_path='checkpoints/last_cnn.ckpt'):
        self.max_steps = max_steps
        self.saver = tf.train.Saver()
        print('Training')

        with tf.Session() as sess:
            self.initialize_vars(sess)
            self.train(sess,data, eval=eval)

            self.saver.save(sess, output_path)
            print('Model saved at %s' % output_path)

    def run_eval(self, eval_data):
        self.saver = tf.train.Saver()
        with tf.Session() as sess:
            self.saver.restore(sess, self.model_checkpoint)
            self.eval(sess, eval_data)
    
    def run_predict(self, eval_data):
        saver = tf.train.Saver()
        if self.session is None:
            self.session = tf.Session()
            self.initialize_vars(self.session)
            saver.restore(self.session, self.model_checkpoint)
        return self.predict(self.session, eval_data)

    def run_model(self, eval_data):
        saver = tf.train.Saver()
        if self.session is None:
            self.session = tf.Session()
            self.initialize_vars(self.session)
            saver.restore(self.session, self.model_checkpoint)
        return self._run_model(self.session, eval_data)
    
    def _run_model(self, sess, data):
        data.eval.run_single(sess)
        self.model.eval(sess)
        input_img, landmarks = self.model.run_model(sess)
        return input_img, landmarks


    def initialize_vars(self, sess):
        init = tf.global_variables_initializer()
        init_l = tf.local_variables_initializer()

        sess.run(init)
        sess.run(init_l)
    
    def predict(self, sess, data):
        data.eval.run_single(sess)
        return self.predict_step(sess, data)

    def eval(self, sess, data):
        self.eval_losses = {}
        self.eval_steps = 0
        data.eval.run_single(sess)
        while True:
            try:
                self.eval_step(sess, data)
            except tf.errors.OutOfRangeError:
                s = ''
                for loss in self.eval_losses:
                    avg_loss = self.eval_losses[loss] / self.eval_steps
                    if loss == 'heatmaps_mse' and avg_loss < self.best_loss:
                        self.saver.save(sess, 'checkpoints/best_cnn.ckpt')
                        self.best_loss = avg_loss

                    s += 'Evaluation Loss %s: %g' % (loss, avg_loss)
                    s += '  |  '
                print(s)
                break

    def train(self, sess, data, eval=True):
        self.data = data
        data.train.run(sess)
        for self.step in range(self.max_steps):
            self.train_step(sess, data, eval=eval)

    def train_step(self, sess, data, eval=True):
        self.train_batch(sess, data)
        if eval and ((self.running_steps % self.eval_steps_mod == 0) or (self.running_steps + 1 == self.max_steps) or self.running_steps == 1):
            self.eval_step_train(sess, data.train)
            self.eval(sess, data)
            data.train.run(sess)

    def train_batch(self, sess, data):
        self.model.train(sess)
        summary, _, losses = self.model.train_iteration(sess)
        self.model.train_writer.add_summary(summary, self.running_steps)
        for key in losses:
            if key not in self.running_losses:
                self.running_losses[key] = 0
            self.running_losses[key] += losses[key]
        self.running_steps += 1


    def eval_step_train(self, sess, data):
        self.model.eval(sess)
        s = ''
        for loss in self.running_losses:
            s += 'Running Loss %s: %g' % (loss, self.running_losses[loss] / self.running_steps)
            s += '  |  '
        self.print_progress(s)

    def eval_step(self, sess, data, train=True):
        self.model.eval(sess)
        summary, _, losses = self.model.eval_iteration(sess)
        self.model.eval_writer.add_summary(summary, self.running_steps)
        for key in losses:
            if key not in self.eval_losses:
                self.eval_losses[key] = 0
            self.eval_losses[key] += losses[key]
        self.eval_steps += 1
    

    def predict_step(self, sess, data, train=True):
        self.model.eval(sess)
        _, output, losses = self.model.eval_iteration(sess)
        return output, losses
    

    def print_progress(self, status):
        util.print_progress_bar(self.step + 1, self.max_steps, status)