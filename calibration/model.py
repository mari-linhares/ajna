import sys
sys.path.append('..')

# neccessary imports
import cv2
import imutils
import util.util as util
import numpy as np
import dlib

import math

import os
import tensorflow as tf

from webcam.webcam_stream import WebcamVideoStream

from data_sources.img_data_source import ImgDataSource
from preprocessing.img_preprocessor import ImgPreprocessor
from models.cnn import CNN


# Function for creating landmark coordinate list
def land2coords(landmarks, dtype="int"):
    # initialize the list of tuples
    # (x, y)-coordinates
    coords = np.zeros((68, 2), dtype=dtype)

    # loop over the eyes landmarks and convert them
    # to a 2-tuple of (a, b)-coordinates
    for i in range(36, 48):
        coords[i] = (landmarks.part(i).x, landmarks.part(i).y)

    # return the list of (a, b)-coordinates
    return coords


def get_eye_info(args, landmarks, frame_gray):
    # Segment eyes
    oh, ow = tuple(args.eye_shape)
    eyes = []
    # for corner1, corner2, is_left in [(2, 3, True), (0, 1, False)]:
    for corner1, corner2, is_left in [(36, 39, True), (42, 45, False)]:
        x1, y1 = landmarks[corner1, :]
        x2, y2 = landmarks[corner2, :]
        eye_width = 1.5 * \
            np.linalg.norm(landmarks[corner1, :] - landmarks[corner2, :])
        if eye_width == 0.0:
            continue
        cx, cy = 0.5 * (x1 + x2), 0.5 * (y1 + y2)

        # Centre image on middle of eye
        translate_mat = np.asmatrix(np.eye(3))
        translate_mat[:2, 2] = [[-cx], [-cy]]
        inv_translate_mat = np.asmatrix(np.eye(3))
        inv_translate_mat[:2, 2] = -translate_mat[:2, 2]

        # Rotate to be upright
        roll = 0.0 if x1 == x2 else np.arctan((y2 - y1) / (x2 - x1))
        rotate_mat = np.asmatrix(np.eye(3))
        cos = np.cos(-roll)
        sin = np.sin(-roll)
        rotate_mat[0, 0] = cos
        rotate_mat[0, 1] = -sin
        rotate_mat[1, 0] = sin
        rotate_mat[1, 1] = cos
        inv_rotate_mat = rotate_mat.T

        # Scale
        scale = ow / eye_width
        scale_mat = np.asmatrix(np.eye(3))
        scale_mat[0, 0] = scale_mat[1, 1] = scale
        inv_scale = 1.0 / scale
        inv_scale_mat = np.asmatrix(np.eye(3))
        inv_scale_mat[0, 0] = inv_scale_mat[1, 1] = inv_scale

        # Centre image
        centre_mat = np.asmatrix(np.eye(3))
        centre_mat[:2, 2] = [[0.5 * ow], [0.5 * oh]]
        inv_centre_mat = np.asmatrix(np.eye(3))
        inv_centre_mat[:2, 2] = -centre_mat[:2, 2]

        # Get rotated and scaled, and segmented image
        transform_mat = centre_mat * scale_mat * rotate_mat * translate_mat
        inv_transform_mat = (inv_translate_mat * inv_rotate_mat * inv_scale_mat *
                             inv_centre_mat)
        eye_image = cv2.warpAffine(frame_gray, transform_mat[:2, :], (ow, oh))

        if is_left:
            eye_image = np.fliplr(eye_image)

        eyes.append({
            'image': eye_image,
            'inv_landmarks_transform_mat': inv_transform_mat,
            'side': 'left' if is_left else 'right',
        })
    return eyes


def estimate_gaze(gaze_history, eye, heatmaps, face_landmarks, eye_landmarks, eye_radius, face, frame_rgb):
    # Gaze estimation
    heatmaps_amax = np.amax(heatmaps.reshape(-1, 18), axis=0)
    can_use_eye = np.all(heatmaps_amax > 0.7)
    can_use_eyelid = np.all(heatmaps_amax[0:8] > 0.75)
    can_use_iris = np.all(heatmaps_amax[8:16] > 0.8)
    bgr = frame_rgb
    # bgr = cv2.flip(bgr, flipCode=1)
    eye_image = eye['image']
    eye_side = eye['side']
    # eye_landmarks = landmarks
    # eye_radius = radius
    if eye_side == 'left':
        eye_landmarks[:, 0] = eye_image.shape[1] - eye_landmarks[:, 0]
        eye_image = np.fliplr(eye_image)

    # Embed eye image and annotate for picture-in-picture
    eye_upscale = 2
    eye_image_raw = cv2.cvtColor(
        cv2.equalizeHist(eye_image), cv2.COLOR_GRAY2BGR)
    eye_image_raw = cv2.resize(
        eye_image_raw, (0, 0), fx=eye_upscale, fy=eye_upscale)
    eye_image_annotated = np.copy(eye_image_raw)

    if can_use_eyelid:
        cv2.polylines(
            eye_image_annotated,
            [np.round(eye_upscale*eye_landmarks[0:8]).astype(np.int32)
             .reshape(-1, 1, 2)],
            isClosed=True, color=(255, 255, 0), thickness=1, lineType=cv2.LINE_AA,
        )
    if can_use_iris:
        cv2.polylines(
            eye_image_annotated,
            [np.round(eye_upscale*eye_landmarks[8:16]).astype(np.int32)
             .reshape(-1, 1, 2)],
            isClosed=True, color=(0, 255, 255), thickness=1, lineType=cv2.LINE_AA,
        )
        cv2.drawMarker(
            eye_image_annotated,
            tuple(np.round(eye_upscale*eye_landmarks[16, :]).astype(np.int32)),
            color=(0, 255, 255), markerType=cv2.MARKER_CROSS, markerSize=4,
            thickness=1, line_type=cv2.LINE_AA,
        )

        # face_index = int(eye_index / 2)
        face_index = 0
        eh, ew, _ = eye_image_raw.shape
        v0 = face_index * 2 * eh
        v1 = v0 + eh
        v2 = v1 + eh
        u0 = 0 if eye_side == 'left' else ew
        u1 = u0 + ew
        bgr[v0:v1, u0:u1] = eye_image_raw
        bgr[v1:v2, u0:u1] = eye_image_annotated

        # Visualize preprocessing results
        for landmark in face_landmarks[:-1]:
            cv2.drawMarker(bgr, tuple(np.round(landmark).astype(np.int32)),
                           color=(0, 0, 255), markerType=cv2.MARKER_STAR,
                           markerSize=2, thickness=1, line_type=cv2.LINE_AA)
            cv2.rectangle(
                bgr, tuple(np.round(face[:2]).astype(np.int32)),
                tuple(np.round(face[2:]).astype(np.int32)),
                color=(0, 255, 255), thickness=1, lineType=cv2.LINE_AA,
            )

        eye_landmarks = np.concatenate(
            [eye_landmarks, [[eye_landmarks[-1, 0] + eye_radius, eye_landmarks[-1, 1]]]])
        eye_landmarks = np.asmatrix(np.pad(eye_landmarks, ((0, 0), (0, 1)),
                                           'constant', constant_values=1.0))
        eye_landmarks = (eye_landmarks *
                         eye['inv_landmarks_transform_mat'].T)[:, :2]
        eye_landmarks = np.asarray(eye_landmarks)
        eyelid_landmarks = eye_landmarks[0:8, :]
        iris_landmarks = eye_landmarks[8:16, :]
        iris_centre = eye_landmarks[16, :]
        eyeball_centre = eye_landmarks[17, :]
        eyeball_radius = np.linalg.norm(eye_landmarks[18, :] -
                                        eye_landmarks[17, :])

        if can_use_eye:
            # Visualize landmarks
            cv2.drawMarker(  # Eyeball centre
                bgr, tuple(np.round(eyeball_centre.astype(
                    np.float32)).astype(np.int32)),
                color=(255, 0, 0),
                markerType=cv2.MARKER_CROSS, markerSize=4,
                thickness=1, line_type=cv2.LINE_AA,
            )

            i_x0, i_y0 = iris_centre
            e_x0, e_y0 = eyeball_centre
            theta = -np.arcsin(np.clip((i_y0 - e_y0) /
                                       eyeball_radius, -1.0, 1.0))
            phi = np.arcsin(np.clip((i_x0 - e_x0) / (eyeball_radius * -np.cos(theta)),
                                    -1.0, 1.0))
            current_gaze = np.array([theta, phi])
            gaze_history.append(current_gaze)
            gaze_history_max_len = 10
            if len(gaze_history) > gaze_history_max_len:
                gaze_history = gaze_history[-gaze_history_max_len:]
            util.draw_gaze(bgr, iris_centre, np.mean(gaze_history, axis=0),
                           length=120.0, thickness=1)

            return bgr, gaze_history, current_gaze
        else:
            return bgr, gaze_history, None
    else:
        return bgr, gaze_history, None


def detect_eye_landmarks(image_np, datasource, preprocessor, sess, model):
    preprocessed_image = preprocessor.preprocess_entry(image_np)
    datasource.image = preprocessed_image
    datasource.run_single(sess)
    model.eval(sess)
    input_image, eye_landmarks, eye_heatmaps, eye_radius = model.run_model(
        sess)
    assert np.all(input_image == preprocessed_image), (input_image,
                                                       preprocessed_image)
    return eye_landmarks, eye_heatmaps, eye_radius


def setup(args):
    # Load a Tensorflow model into memory.
    # If needed froze the graph to get better performance.
    data_format = args.data_format
    shape = tuple(args.eye_shape)
    preprocessor = ImgPreprocessor(data_format)
    datasource = ImgDataSource(shape=shape,
                               data_format=data_format)
    # Get model
    model = CNN(datasource.tensors, datasource.x_shape, None,
                data_format=data_format, predict_only=True)

    # Start session
    saver = tf.train.Saver()
    sess = tf.Session()

    # Init variables
    init = tf.global_variables_initializer()
    init_l = tf.local_variables_initializer()
    sess.run(init)
    sess.run(init_l)
    # Restore model checkpoint
    saver.restore(sess, args.model_checkpoint)

    return datasource, preprocessor, sess, model


def worker(input_q, output_q):
    datasource, preprocessor, sess, model = setup()
    while True:
        x = input_q.get()
        output_q.put(detect_eye_landmarks(
            x, datasource, preprocessor, sess, model))
    sess.close()


def distance_to_camera(knownWidth, focalLength, perWidth):
        # compute and return the distance from the maker to the camera
    return (knownWidth * focalLength) / perWidth


def estimate(eye1):
    point1 = to_3D(eye1.coordinates)
    print(point1)


# Converte uma coordenada na imagem da webcam para um ponto 3D
def to_3D(point):
    x, y = point
    return zero + Point(x, 0, y)


class Model:
    def __init__(self, args):
        self.args = args

        # Start video capture
        self.video_capture = WebcamVideoStream(src=args.video_source).start()

        # loading dlib's Hog Based face detector
        self.face_detector = dlib.get_frontal_face_detector()
        # loading dlib's 68 points-shape-predictor
        # get file:shape_predictor_68_face_landmarks.dat from
        # link: https://drive.google.com/firun_prele/d/1XvAobn_6xeb8Ioa8PBnpCXZm8mgkBTiJ/view?usp=sharing
        self.landmark_predictor = dlib.shape_predictor(args.model_crop_eyes)

        self.gaze_history = []

        self.datasource, self.preprocessor, self.sess, self.model = setup(args)

    def run(self):
        result = []

        frame = self.video_capture.read()
        frame = imutils.resize(frame, width=800)

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # grayscale conversion of image because it is computationally efficient
        # to perform operations on single channeled (grayscale) image
        frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # detecting faces
        face_boundaries = self.face_detector(frame_gray, 0)
        # if there's no face do nothing
        if len(face_boundaries) < 1:
            return frame_rgb, None

        face = face_boundaries[0]
        # for face in face_boundaries:
        # Let's predict the landmarks
        landmarks = self.landmark_predictor(frame_gray, face)
        # converting co-ordinates to NumPy array
        landmarks = land2coords(landmarks)

        eyes = get_eye_info(self.args, landmarks, frame_gray)
        face = (face.left(), face.top(), face.right(), face.bottom())
        for eye in eyes:
            eye_landmarks, heatmaps, eye_radius = detect_eye_landmarks(eye['image'], self.datasource, self.preprocessor, self.sess, self.model)
            eye_landmarks = eye_landmarks.reshape(18, 2)
            bgr, self.gaze_history, gaze = estimate_gaze(
                self.gaze_history, eye, heatmaps, landmarks, eye_landmarks, eye_radius, face, frame_rgb)

            for (a, b) in landmarks.reshape(-1, 2):
                cv2.circle(frame, (a, b), 2, (0, 255, 0), -1)

            if gaze is not None:
                result.append((gaze[0][0][0], gaze[1][0][0]))

        return bgr, result

    def close(self):
        self.video_capture.stop()
        cv2.destroyAllWindows()
        self.sess.close()
