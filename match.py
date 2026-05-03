import os
import cv2
import dlib
from scipy.spatial import distance as dist
import pickle
import numpy as np
import face_recognition

detector = dlib.get_frontal_face_detector()
predictor = dlib.shape_predictor("shape_predictor_68_face_landmarks.dat")

def eye_aspect_ratio(eye):
    A = dist.euclidean(eye[1], eye[5])
    B = dist.euclidean(eye[2], eye[4])
    C = dist.euclidean(eye[0], eye[3])
    ear = (A + B) / (2.0 * C)
    return ear

def train():
    dataset_directory = 'dataset'
    known_encodings = []
    known_names = []

    for subdir in os.listdir(dataset_directory):
        subdir_path = os.path.join(dataset_directory, subdir)
        if os.path.isdir(subdir_path):
            for filename in os.listdir(subdir_path):
                if filename.endswith(('.jpg', '.png', '.jpeg')):
                    image_path = os.path.join(subdir_path, filename)
                    image = face_recognition.load_image_file(image_path)
                    faces = face_recognition.face_locations(image)
                    encodings = face_recognition.face_encodings(image, faces)
                    for encoding in encodings:
                        known_encodings.append(encoding)
                        known_names.append(subdir)

    data = {'encodings': known_encodings, 'names': known_names}
    with open('encodings.pickle', 'wb') as f:
        pickle.dump(data, f)
    print("Training complete!")

def match_frame(frame):
    if not os.path.exists('encodings.pickle'):
        return "Not trained yet", False, 0.0

    with open('encodings.pickle', 'rb') as f:
        data = pickle.load(f)
    known_encodings = data['encodings']
    known_names = data['names']

    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    faces = face_recognition.face_locations(rgb_frame)
    encodings = face_recognition.face_encodings(rgb_frame, faces)

    name = "Unknown"
    blink_detected = False
    ear_value = 0.0

    if len(faces) == 0:
        return "No face detected", False, 0.0
    elif len(faces) > 1:
        return "Multiple faces detected", False, 0.0

    for encoding, (top, right, bottom, left) in zip(encodings, faces):
        matches = face_recognition.compare_faces(known_encodings, encoding)
        if True in matches:
            match_index = matches.index(True)
            name = known_names[match_index]

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            dlib_faces = detector(gray)
            for face in dlib_faces:
                shape = predictor(gray, face)
                shape = np.array([(shape.part(i).x, shape.part(i).y) for i in range(68)])
                left_eye = shape[36:42]
                right_eye = shape[42:48]
                left_ear = eye_aspect_ratio(left_eye)
                right_ear = eye_aspect_ratio(right_eye)
                ear_value = (left_ear + right_ear) / 2.0

                # strict blink: eyes must clearly close
                if ear_value < 0.25:
                    blink_detected = True

    return name, blink_detected, ear_value