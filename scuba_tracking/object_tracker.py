#!/usr/bin/env python
############################
# This code is the first version which:
# Grabs images from the Game engine and can publish the final command to control the robot
# Next step: this should be where we use a really simple approach to track the target
############################
import rclpy
from cv_bridge import CvBridge
from sensor_msgs.msg import CompressedImage
import cv2
from rclpy.node import Node
from src.scuba_tracking.scuba_tracking.models.yolov7 import YoloV7
from std_msgs.msg import String
import os, time, threading
from src.scuba_tracking.scuba_tracking.config import config

flag_updated_frame = False # this flag is used to be sure that the detector does not process the same image
string_command = ''
img = None

class object_tracker(Node):

    def __init__(self):
        super().__init__('object_tracker')

        print('cam topic: ',config.CAMERA_TOPIC)
        self.image_subscription = self.create_subscription(
            CompressedImage,
            config.CAMERA_TOPIC,
            self.image_handler,
            30)
        self.data_publisher = self.create_publisher(String, config.GENERATED_BB_TOPIC, 30)
        self.msg_ = String()
        return

    # We separated the subscription part to address the freezing issue we had in the experiments
    def image_handler(self, msg):
        global img, string_command, flag_updated_frame
        img = CvBridge().compressed_imgmsg_to_cv2(msg)
        self.msg_.data = string_command
        self.data_publisher.publish(self.msg_)
        flag_updated_frame = True

def image_processing(record = False):
    global img, string_command, flag_updated_frame
    # initiating the detector in a distinct thread
    detector = YoloV7()
    if record:
        num_of_videos = 0
        path_to_recorded_video = './resulted_tracking/'
        if not os.path.isdir(path_to_recorded_video):
            os.mkdir(path_to_recorded_video)
        else:
            num_of_videos = len(os.listdir(path_to_recorded_video))

        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        frame_size = config.IMAGE_SIZE
        out = cv2.VideoWriter(path_to_recorded_video + 'tracking_scenario_'+str(num_of_videos)+'.avi', fourcc, 30.0, frame_size)
    cv2.namedWindow("Processed frames", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Processed frames", config.IMAGE_SIZE[0], config.IMAGE_SIZE[1])
    while(1):
        if img is None or not(flag_updated_frame):
            continue
        else:
            last_time = time.time()
            string_output, outputs, img_ = detector.detect(img)
            string_command = str(len(outputs)) + string_output
            print(time.time() - last_time)
            cv2.imshow("Processed frames", img_)
            key_ = cv2.waitKey(1)
            if key_ == ord('q'):  # quit
               break
            if record:
                out.write(img_)
            flag_updated_frame = False

def ros_node():
    rclpy.init()

    object_tracker_ = object_tracker()

    rclpy.spin(object_tracker_)

    object_tracker_.destroy_node()
    rclpy.shutdown()

def main(args=None):
    object_detector_thread = threading.Thread(target=ros_node)
    object_detector_thread.start()
    print('Vision node initiated!')
    #################################################
    image_processing()

if __name__ == '__main__':
    main()
