import numpy as np
from filterpy.kalman import KalmanFilter
from scipy.optimize import linear_sum_assignment

def linear_assignment(cost_matrix):
    x,y = linear_sum_assignment(cost_matrix)
    return np.array(list(zip(x,y)))
    # alternative to scipy linear_sum_assignment
    # import lap
    # _, x, y = lap.lapjv(cost_matrix, extend_cost=True)
    # return np.array([[y[i],i] for i in x if i >= 0]) 

def iou_batch(bb_test, bb_gt):
    
    bb_gt = np.expand_dims(bb_gt, 0)
    bb_test = np.expand_dims(bb_test, 1)
    
    xx1 = np.maximum(bb_test[...,0], bb_gt[..., 0])
    yy1 = np.maximum(bb_test[..., 1], bb_gt[..., 1])
    xx2 = np.minimum(bb_test[..., 2], bb_gt[..., 2])
    yy2 = np.minimum(bb_test[..., 3], bb_gt[..., 3])
    w = np.maximum(0., xx2 - xx1)
    h = np.maximum(0., yy2 - yy1)
    wh = w * h
    o = wh / ((bb_test[..., 2] - bb_test[..., 0]) * (bb_test[..., 3] - bb_test[..., 1])                                      
    + (bb_gt[..., 2] - bb_gt[..., 0]) * (bb_gt[..., 3] - bb_gt[..., 1]) - wh)
    return(o)


#input [x1,y1,x2,y2,conf], return z in the form [x,y,s,r,conf] where x,y is the center of the box, s is the scale/area, r is the aspect ratio, and conf is the confidence
def convert_bbox_to_z(bbox):
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = bbox[0] + w/2.
    y = bbox[1] + h/2.
    s = w * h 
    conf = bbox[4]   
    #scale is just area
    r = w / float(h)
    return np.array([x, y, s, r, conf]).reshape((5, 1))


#input bounding box of form [x,y,s,r,conf] and returns it in the form [x1,y1,x2,y2,conf]
def convert_x_to_bbox(x):
    w = np.sqrt(x[2] * x[3])
    h = x[2] / w
    return np.array([x[0]-w/2.,x[1]-h/2.,x[0]+w/2.,x[1]+h/2.,x[4]]).reshape((1,5))

#represents the internal state of individual tracked objects observed as bbox
class KalmanBoxTracker(object):
    
    count = 0
    def __init__(self, bbox):
        #initialize a tracker using initial bounding box
        #bbox must have detected class number at the last array position.
        #constant velocity model 
        self.kf = KalmanFilter(dim_x=8, dim_z=5)
        self.kf.F = np.array([[1,0,0,0,0,1,0,0],[0,1,0,0,0,0,1,0],[0,0,1,0,0,0,0,1],[0,0,0,1,0,0,0,0],[0,0,0,0,1,0,0,0],[0,0,0,0,0,1,0,0],[0,0,0,0,0,0,1,0],[0,0,0,0,0,0,0,1]])
        self.kf.H = np.array([[1,0,0,0,0,0,0,0],[0,1,0,0,0,0,0,0],[0,0,1,0,0,0,0,0],[0,0,0,1,0,0,0,0],[0,0,0,0,1,0,0,0]])

        self.kf.R[2:,2:] *= 10. # R: Covariance matrix of measurement noise (set to high for noisy inputs -> more 'inertia' of boxes')
        self.kf.P[5:,5:] *= 1000. #give high uncertainty to the unobservable initial velocities
        self.kf.P *= 10.
        self.kf.Q[-1,-1] *= 0.5 # Q: Covariance matrix of process noise (set to high for erratically moving things)
        self.kf.Q[5:,5:] *= 0.5

        self.kf.x[:5] = convert_bbox_to_z(bbox) # STATE VECTOR
        self.time_since_update = 0
        self.id = KalmanBoxTracker.count
        KalmanBoxTracker.count += 1
        self.history = []
        self.hits = 0
        self.hit_streak = 0
        self.age = 0
        self.centroidarr = []
        CX = (bbox[0]+bbox[2])//2
        CY = (bbox[1]+bbox[3])//2
        self.centroidarr.append((CX,CY))
        self.detclass = bbox[5]
        self.bbox_history = [bbox]
        
    def update(self, bbox):
        #updates the state vector with observed bbox
        self.time_since_update = 0
        self.history = []
        self.hits += 1
        self.hit_streak += 1
        self.kf.update(convert_bbox_to_z(bbox))
        
        self.detclass = bbox[5]
        CX = (bbox[0]+bbox[2])//2
        CY = (bbox[1]+bbox[3])//2
        self.centroidarr.append((CX,CY))
        self.bbox_history.append(bbox)
    
    def predict(self):
        #advances the state vector and returns the predicted bounding box estimate
        if((self.kf.x[7]+self.kf.x[2])<=0):
            self.kf.x[7] *= 0.0
        self.kf.predict()
        self.age += 1
        if(self.time_since_update>0):
            self.hit_streak = 0
        self.time_since_update += 1
        self.history.append(convert_x_to_bbox(self.kf.x))       
        return self.history[-1]
    
    
    def get_state(self):
        #returns the current bounding box estimate
        arr_detclass = np.expand_dims(np.array([self.detclass]), 0)
        arr_u_dot = np.expand_dims(self.kf.x[5],0)
        arr_v_dot = np.expand_dims(self.kf.x[6],0)
        arr_s_dot = np.expand_dims(self.kf.x[7],0)
        
        return np.concatenate((convert_x_to_bbox(self.kf.x), arr_detclass, arr_u_dot, arr_v_dot, arr_s_dot), axis=1)
    
def associate_detections_to_trackers(detections, trackers, iou_threshold = 0.3):
    
    #assigns detections to tracked object (both represented as bounding boxes)
    #returns 3 lists of 1) matches, 2) unmatched detections, 3) unmatched trackers
    if(len(trackers)==0):
        return np.empty((0,2),dtype=int), np.arange(len(detections)), np.empty((0,5),dtype=int)
    
    iou_matrix = iou_batch(detections, trackers)
    
    if min(iou_matrix.shape) > 0:
        a = (iou_matrix > iou_threshold).astype(np.int32)
        if a.sum(1).max() == 1 and a.sum(0).max() ==1:
            matched_indices = np.stack(np.where(a), axis=1)
        else:
            matched_indices = linear_assignment(-iou_matrix)
    else:
        matched_indices = np.empty(shape=(0,2))
    
    unmatched_detections = []
    for d, det in enumerate(detections):
        if(d not in matched_indices[:,0]):
            unmatched_detections.append(d)
    
    unmatched_trackers = []
    for t, trk in enumerate(trackers):
        if(t not in matched_indices[:,1]):
            unmatched_trackers.append(t)
    
    #filter out matched with low IOU
    matches = []
    for m in matched_indices:
        if(iou_matrix[m[0], m[1]]<iou_threshold):
            unmatched_detections.append(m[0])
            unmatched_trackers.append(m[1])
        else:
            matches.append(m.reshape(1,2))
    
    if(len(matches)==0):
        matches = np.empty((0,2), dtype=int)
    else:
        matches = np.concatenate(matches, axis=0)
        
    return matches, np.array(unmatched_detections), np.array(unmatched_trackers)
    

class Sort(object):
    def __init__(self, max_age=1, min_hits=3, iou_threshold=0.3):

        self.max_age = max_age #number of frames to keep using prior detection
        self.min_hits = min_hits #number of hits to declare new detection 
        self.iou_threshold = iou_threshold #min iou similarity to associate detections to trackers

        self.trackers = []
        self.frame_count = 0

    def getTrackers(self,):
        return self.trackers
        
    def update(self, dets= np.empty((0,6))):
        self.frame_count += 1
        # get predicted locations from existing trackers
        trks = np.zeros((len(self.trackers), 6))
        to_del = []
        ret = []
        for t, trk in enumerate(trks):
            pos = self.trackers[t].predict()[0]
            trk[:] = [pos[0], pos[1], pos[2], pos[3], pos[4], 0]
            if np.any(np.isnan(pos)):
                to_del.append(t)
        trks = np.ma.compress_rows(np.ma.masked_invalid(trks))
        for t in reversed(to_del):
            self.trackers.pop(t)
        matched, unmatched_dets, unmatched_trackers = associate_detections_to_trackers(dets, trks, self.iou_threshold)

        # Update matched trackers with assigned detections
        for m in matched:
            self.trackers[m[1]].update(dets[m[0], :])
            
        # Create and initialize new trackers for unmatched detections
        for i in unmatched_dets:
            trk = KalmanBoxTracker(np.hstack((dets[i,:], np.array([0]))))
            self.trackers.append(trk)
        
        i = len(self.trackers)

        for trk in reversed(self.trackers):
            d = trk.get_state()[0]
            #if time since last detection < self.max_age and if number of detections is >= self.min_hits 
            if ((trk.time_since_update < self.max_age) and (trk.hits >= self.min_hits)):
                ret.append(np.concatenate((d, [trk.id])).reshape(1,-1))
            else:
                print('removed noise (id, time_since_last_detect, num_detections) = ', trk.id, trk.time_since_update, trk.hits)
            i -= 1
            if(trk.time_since_update > self.max_age):
                self.trackers.pop(i)
        if(len(ret) > 0):
            return np.concatenate(ret)
        return np.empty((0,6))